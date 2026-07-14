"""Stable connector-family native-shape contracts (ADRs 0033–0034).

These models describe reusable read shapes, not provider payloads. Service
packs map their APIs into one family; product clients render the validated
shape through curated blocks.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


ConnectorFamily = Literal[
    "conversation", "schedule", "records", "documents", "telemetry"
]
NativeViewState = Literal["empty", "ready", "partial", "stale", "failed"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationThreadSummary(_StrictModel):
    thread_ref: str = Field(min_length=1)
    title: str = ""
    participant_refs: list[str] = Field(default_factory=list)
    last_message_at: Optional[str] = None
    unread_count: int = Field(default=0, ge=0)
    message_count: int = Field(default=0, ge=0)
    latest_message_ref: Optional[str] = None
    latest_sender: Optional[str] = None
    preview: str = ""
    interpretation_state: Literal[
        "ready", "selected", "completed", "deterministic_only",
        "held", "suppressed", "empty", "failed",
    ] = "empty"
    status: Literal["open", "closed", "archived"] = "open"
    refs: list[str] = Field(default_factory=list)
    # Learned-salience refs (ADR 0038): opaque ids into the attention vector
    # space, attached once reply/person signals exist. The first live run
    # failed 100-thread validation here because the projector emitted them
    # before the contract carried the field.
    attention_refs: list[str] = Field(default_factory=list)


class ConversationNativeData(_StrictModel):
    threads: list[ConversationThreadSummary] = Field(default_factory=list)
    total_count: int = Field(default=0, ge=0)


class AgendaOccurrence(_StrictModel):
    occurrence_ref: str = Field(min_length=1)
    title: str = ""
    start: Optional[str] = None
    end: Optional[str] = None
    timezone: Optional[str] = None
    status: Literal["confirmed", "tentative", "cancelled"] = "confirmed"
    participant_refs: list[str] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)


class AgendaNativeData(_StrictModel):
    occurrences: list[AgendaOccurrence] = Field(default_factory=list)
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    total_count: int = Field(default=0, ge=0)


class RecordColumn(_StrictModel):
    key: str = Field(min_length=1)
    label: str = Field(min_length=1)


class RecordsNativeData(_StrictModel):
    columns: list[RecordColumn] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    total_count: int = Field(default=0, ge=0)


class LibraryItem(_StrictModel):
    item_ref: str = Field(min_length=1)
    title: str = ""
    kind: str = "document"
    parent_ref: Optional[str] = None
    revision_ref: Optional[str] = None
    updated_at: Optional[str] = None
    refs: list[str] = Field(default_factory=list)


class LibraryNativeData(_StrictModel):
    items: list[LibraryItem] = Field(default_factory=list)
    total_count: int = Field(default=0, ge=0)


class TelemetryPoint(_StrictModel):
    bucket: str = Field(min_length=1)
    label: str = ""
    value: float
    refs: list[str] = Field(default_factory=list)


class TelemetryNativeData(_StrictModel):
    points: list[TelemetryPoint] = Field(default_factory=list)
    unit: str = "count"


NATIVE_DATA_MODELS = {
    "conversation": ConversationNativeData,
    "schedule": AgendaNativeData,
    "records": RecordsNativeData,
    "documents": LibraryNativeData,
    "telemetry": TelemetryNativeData,
}


def validate_native_data(family: str, data: dict[str, Any]) -> dict[str, Any]:
    """Validate and canonicalize one family-native payload."""
    try:
        model = NATIVE_DATA_MODELS[family]
    except KeyError as exc:
        raise ValueError(f"unknown connector family {family!r}") from exc
    return model.model_validate(data).model_dump()


__all__ = [
    "ConnectorFamily",
    "NativeViewState",
    "NATIVE_DATA_MODELS",
    "validate_native_data",
]
