"""WhatsApp Adapter Pack object and relation types — v0.1.

One object type: the raw inbound message, injected at the edge by the
webhook receiver (Meta's WhatsApp Cloud API delivers via webhooks; there
is no long-poll). Everything conversational happens in the Chat Pack —
this pack is a transport adapter, nothing more. Structurally the mirror of
the Telegram Adapter Pack; the two differ only in wire shapes.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from activegraph.packs import ObjectType, RelationType


class WhatsAppMessage(BaseModel):
    """A raw inbound WhatsApp message, as injected by the webhook driver.

    Only the fields the adapter needs are lifted out; the trimmed original
    lives in ``raw`` for audit. No tokens or secrets ever appear here.
    """

    message_id: str = Field(description="WhatsApp message id (wamid…) — the dedup key.")
    from_number: str = Field(description="Sender's phone number (E.164, no '+').")
    profile_name: Optional[str] = Field(default=None, description="Sender's WhatsApp profile name.")
    text: str = Field(default="", description="Message body (empty for non-text messages).")
    timestamp: Optional[str] = Field(default=None, description="Provider timestamp.")
    received_at: Optional[str] = Field(default=None, description="ISO 8601 arrival time.")
    raw: dict[str, Any] = Field(
        default_factory=dict,
        description="Trimmed original message payload, for audit.",
    )
    frame_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


OBJECT_TYPES = [
    ObjectType(
        name="whatsapp_message",
        schema=WhatsAppMessage,
        description=(
            "A raw inbound WhatsApp Cloud API message injected by the webhook "
            "driver. whatsapp_ingester translates it into a chat_input, which "
            "is where the conversation machinery begins."
        ),
    ),
]

RELATION_TYPES = [
    RelationType(
        name="wa_ingested_as",
        source_types=("whatsapp_message",),
        target_types=("chat_input",),
        description="A WhatsApp message was ingested as a chat_input.",
    ),
]
