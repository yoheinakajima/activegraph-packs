"""Gmail-specific sync and local-draft state; evidence stays provider-neutral."""

from __future__ import annotations

from typing import Any, Literal, Optional

from activegraph.packs import ObjectType, RelationType
from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GmailSyncRun(_StrictModel):
    run_identity: str = Field(min_length=1)
    source_surface_id: str = Field(min_length=1)
    account_ref: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    connected_account_id: str = Field(min_length=1)
    mode: Literal["backfill", "poll"]
    query: str = ""
    start_history_id: Optional[str] = None
    next_page_token: Optional[str] = None
    page_size: int = Field(ge=1, le=100)
    max_messages: int = Field(ge=1)
    max_pages: int = Field(ge=1)
    pages_completed: int = Field(default=0, ge=0)
    messages_imported: int = Field(default=0, ge=0)
    call_ids: list[str] = Field(default_factory=list)
    pending_message_ids: list[str] = Field(default_factory=list)
    completed_message_ids: list[str] = Field(default_factory=list)
    missing_message_ids: list[str] = Field(default_factory=list)
    status: Literal["proposed", "running", "completed", "partial", "failed"] = "proposed"
    latest_history_id: Optional[str] = None
    deleted_message_ids: list[str] = Field(default_factory=list)
    tombstones_recorded: int = Field(default=0, ge=0)
    error_code: Optional[
        Literal["rate_limited", "auth_expired", "cursor_invalid", "unexpected_shape", "provider_failed"]
    ] = None
    error: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GmailDraftCandidate(_StrictModel):
    draft_identity: str = Field(min_length=1)
    account_ref: str = Field(min_length=1)
    connected_account_id: str = Field(min_length=1)
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: str = ""
    body: str = ""
    thread_id: Optional[str] = None
    status: Literal["local_draft", "sync_proposed", "synced", "send_proposed", "sent", "rejected"] = "local_draft"
    provider_draft_id: Optional[str] = None
    prediction_id: Optional[str] = None
    idempotency_key: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


OBJECT_TYPES = [
    ObjectType("gmail_sync_run", GmailSyncRun, "One bounded Gmail backfill or history-poll run."),
    ObjectType("gmail_draft_candidate", GmailDraftCandidate, "A local R1 draft before any Gmail write or send."),
]

RELATION_TYPES = [
    RelationType(
        "gmail_sync_call",
        source_types=("gmail_sync_run",),
        target_types=("capability_call",),
        description="A bounded Gmail sync run proposed a recorded provider call.",
    ),
    RelationType(
        "gmail_draft_call",
        source_types=("gmail_draft_candidate",),
        target_types=("capability_call",),
        description="A local draft proposed a Gmail write or send capability.",
    ),
]


__all__ = ["GmailSyncRun", "GmailDraftCandidate", "OBJECT_TYPES", "RELATION_TYPES"]
