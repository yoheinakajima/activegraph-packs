"""WhatsApp Adapter Pack tools — v0.1.

submit_whatsapp_webhook is what the webhook receiver (the demo server's
POST /channels/whatsapp/webhook, or any HTTPS endpoint you point Meta at)
calls with the Cloud API's webhook envelope. It unwraps
entry[].changes[].value.messages[] into whatsapp_message objects;
everything after that is reactive.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from activegraph.packs import tool


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def submit_whatsapp_webhook_fn(
    graph, payload: dict[str, Any], frame_id: Optional[str] = None
) -> list:
    """Unwrap a Cloud API webhook envelope into whatsapp_message objects.

    One envelope can carry several messages (and also status updates, which
    are skipped in v0.1). Returns the created objects.
    """
    created = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            contacts = {
                c.get("wa_id"): (c.get("profile") or {}).get("name")
                for c in value.get("contacts", []) or []
            }
            for message in value.get("messages", []) or []:
                if message.get("type") != "text":
                    continue  # media/reactions/etc. — v0.1 converses in text
                from_number = message.get("from", "")
                created.append(graph.add_object("whatsapp_message", {
                    "message_id": message.get("id", ""),
                    "from_number": from_number,
                    "profile_name": contacts.get(from_number),
                    "text": (message.get("text") or {}).get("body", ""),
                    "timestamp": message.get("timestamp"),
                    "received_at": _now_iso(),
                    "raw": {"type": message.get("type")},
                    "frame_id": frame_id,
                    "metadata": {},
                }))
    return created


@tool(
    name="submit_whatsapp_webhook",
    description=(
        "Inject a WhatsApp Cloud API webhook envelope into the graph — each "
        "text message becomes a whatsapp_message object. The adapter "
        "behaviors take it from there."
    ),
)
def submit_whatsapp_webhook(graph, payload: dict, frame_id: Optional[str] = None) -> list:
    """Registered tool wrapper — delegates to submit_whatsapp_webhook_fn."""
    return submit_whatsapp_webhook_fn(graph, payload, frame_id)


TOOLS = [submit_whatsapp_webhook]
