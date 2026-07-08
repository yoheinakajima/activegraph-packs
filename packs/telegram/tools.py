"""Telegram Adapter Pack tools — v0.1.

submit_telegram_update is what edge drivers (the long-poller, a webhook
receiver, the demo server's /channels/telegram/update endpoint) call to
inject a raw Telegram update into the graph. It normalizes Telegram's
update JSON down to the fields the adapter needs; everything after that is
reactive.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from activegraph.packs import tool


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def submit_telegram_update_fn(graph, update: dict[str, Any], frame_id: Optional[str] = None):
    """Normalize a raw Telegram update and add it as a telegram_update object.

    Accepts the wire shape of the Bot API's getUpdates/webhook payloads
    (``{"update_id": ..., "message": {...}}``). Non-message updates (edits,
    callbacks) return None in v0.1. Returns the created object or None.
    """
    message = update.get("message") or {}
    if not message:
        return None
    chat = message.get("chat") or {}
    user = message.get("from") or {}

    return graph.add_object("telegram_update", {
        "update_id": int(update.get("update_id", 0)),
        "chat_id": str(chat.get("id", "")),
        "user_id": str(user.get("id", "")),
        "username": user.get("username"),
        "text": message.get("text") or "",
        "message_id": message.get("message_id"),
        "received_at": _now_iso(),
        # Keep only what audit needs — no full raw blob bloat.
        "raw": {
            "date": message.get("date"),
            "chat_type": chat.get("type"),
            "language_code": user.get("language_code"),
        },
        "frame_id": frame_id,
        "metadata": {},
    })


@tool(
    name="submit_telegram_update",
    description=(
        "Inject a raw Telegram Bot API update (getUpdates/webhook wire shape) "
        "into the graph as a telegram_update object. The adapter behaviors "
        "take it from there."
    ),
)
def submit_telegram_update(graph, update: dict, frame_id: Optional[str] = None):
    """Registered tool wrapper — delegates to submit_telegram_update_fn."""
    return submit_telegram_update_fn(graph, update, frame_id)


TOOLS = [submit_telegram_update]
