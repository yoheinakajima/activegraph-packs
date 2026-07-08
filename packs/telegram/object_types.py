"""Telegram Adapter Pack object and relation types — v0.1.

One object type: the raw inbound update, injected at the edge by a driver
(long-poller or webhook receiver). Everything conversational happens in
the Chat Pack (sessions, memory, gating, responders) — this pack is a
transport adapter, nothing more.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from activegraph.packs import ObjectType, RelationType


class TelegramUpdate(BaseModel):
    """A raw inbound Telegram update, as injected by the driver.

    Only the fields the adapter needs are lifted out; the trimmed original
    lives in ``raw`` for audit. No tokens or secrets ever appear here —
    the update is user content, credentials stay in the Secrets Pack.
    """

    update_id: int = Field(description="Telegram's monotonically increasing update id (dedup key).")
    chat_id: str = Field(description="Chat to reply into (DM chat id == user id).")
    user_id: str = Field(description="Sender's Telegram user id.")
    username: Optional[str] = Field(default=None, description="Sender's @username, if set.")
    text: str = Field(default="", description="Message text (empty for non-text updates).")
    message_id: Optional[int] = Field(default=None)
    received_at: Optional[str] = Field(default=None, description="ISO 8601 arrival time.")
    raw: dict[str, Any] = Field(
        default_factory=dict,
        description="Trimmed original update payload, for audit.",
    )
    frame_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


OBJECT_TYPES = [
    ObjectType(
        name="telegram_update",
        schema=TelegramUpdate,
        description=(
            "A raw inbound Telegram update injected by the edge driver "
            "(poller/webhook). telegram_ingester translates it into a "
            "chat_input, which is where the conversation machinery begins."
        ),
    ),
]

# The `delivers` relation both messenger dispatchers use is declared by the
# Communication Pack (it targets comm_response_candidate, communication's
# noun) — adapters only create instances of it.
RELATION_TYPES = [
    RelationType(
        name="ingested_as",
        source_types=("telegram_update",),
        target_types=("chat_input",),
        description="A Telegram update was ingested as a chat_input.",
    ),
]
