"""Telegram Adapter Pack gateway capabilities.

Registers ``telegram.send_message`` — the ONLY place this adapter touches
the network, and it runs inside the Tool Gateway's execution path: the bot
token arrives via execution_context (injected by the Secrets Pack at
execution time, never stored), the output is sanitized before recording,
and every send is a first-class capability_call in the audit trail.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class SendMessageInput(BaseModel):
    """Parameters for telegram.send_message."""

    chat_id: str = Field(description="Telegram chat id to send into.")
    text: str = Field(description="Message text.")


def register_send_capability(*, api_base: str = "https://api.telegram.org"):
    """Register telegram.send_message on the gateway (stdlib HTTP only).

    Requires the Tool Gateway Pack (guarded import; returns None without it).
    """
    try:
        from packs.tool_gateway.tools import register_local_capability
    except Exception:
        return None

    def _send_message(
        chat_id: str = "",
        text: str = "",
        execution_context: Optional[dict] = None,
    ) -> dict:
        import json
        import urllib.request

        token = (execution_context or {}).get("credential")
        if not token:
            return {"ok": False, "reason": "no bot token injected — register "
                                           "the TELEGRAM_BOT_TOKEN credential"}

        req = urllib.request.Request(
            f"{api_base}/bot{token}/sendMessage",
            data=json.dumps({"chat_id": chat_id, "text": text}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
        return {
            "ok": bool(body.get("ok")),
            "message_id": (body.get("result") or {}).get("message_id"),
        }

    return register_local_capability(
        "telegram", "send_message", _send_message,
        input_schema=SendMessageInput,
        description="Send a text message to a Telegram chat via the Bot API.",
        risk_class="low",
        credential_ref_name="TELEGRAM_BOT_TOKEN",
    )
