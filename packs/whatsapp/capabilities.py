"""WhatsApp Adapter Pack gateway capabilities.

Registers ``whatsapp.send_message`` — the ONLY place this adapter touches
the network, and it runs inside the Tool Gateway's execution path: the
access token arrives via execution_context (injected by the Secrets Pack
at execution time, never stored), and every send is a first-class
capability_call in the audit trail.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class SendMessageInput(BaseModel):
    """Parameters for whatsapp.send_message."""

    to: str = Field(description="Recipient phone number (E.164, no '+').")
    text: str = Field(description="Message text.")


def register_send_capability(
    *,
    phone_number_id: Optional[str] = None,
    api_base: str = "https://graph.facebook.com/v20.0",
):
    """Register whatsapp.send_message on the gateway (stdlib HTTP only).

    *phone_number_id* is the business number sends originate from —
    deployment configuration, not a secret. Without it the capability
    registers but refuses to send, with the fix in its error.

    Requires the Tool Gateway Pack (guarded import; returns None without it).
    """
    try:
        from packs.tool_gateway.tools import register_local_capability
    except Exception:
        return None

    def _send_message(
        to: str = "",
        text: str = "",
        execution_context: Optional[dict] = None,
    ) -> dict:
        import json
        import urllib.request

        token = (execution_context or {}).get("credential")
        if not token:
            return {"ok": False, "reason": "no access token injected — register "
                                           "the WHATSAPP_ACCESS_TOKEN credential"}
        if not phone_number_id:
            return {"ok": False, "reason": "phone_number_id not configured — pass it "
                                           "to register_send_capability (or set "
                                           "WHATSAPP_PHONE_NUMBER_ID for the demo server)"}

        req = urllib.request.Request(
            f"{api_base}/{phone_number_id}/messages",
            data=json.dumps({
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"body": text},
            }).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
        sent = bool(body.get("messages"))
        return {
            "ok": sent,
            "message_id": (body.get("messages") or [{}])[0].get("id"),
        }

    return register_local_capability(
        "whatsapp", "send_message", _send_message,
        input_schema=SendMessageInput,
        description="Send a text message via the WhatsApp Cloud API.",
        risk_class="low",
        # R3: a delivered message cannot be unsent — an outward,
        # irreversible action regardless of the legacy risk label.
        action_class="R3",
        credential_ref_name="WHATSAPP_ACCESS_TOKEN",
    )
