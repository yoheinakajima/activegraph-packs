"""Neutral connection, coverage, settlement, and activity fact schemas."""

from __future__ import annotations

from typing import Any, Literal, Optional

from activegraph.packs import ObjectType, RelationType
from pydantic import BaseModel, ConfigDict, Field


SOURCE_CATEGORIES = (
    "communication",
    "rhythm",
    "ai_activity",
    "code_work",
    "local_knowledge",
    "tool_automation",
    "outcome_evaluation",
)
SourceCategory = Literal[
    "communication",
    "rhythm",
    "ai_activity",
    "code_work",
    "local_knowledge",
    "tool_automation",
    "outcome_evaluation",
]
ConnectionPath = Literal["export", "mcp", "composio", "native", "local", "pack"]
SurfaceStatus = Literal["connected", "settling", "settled", "stale", "revoked", "failed"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CursorState(_StrictModel):
    oldest_ingested_ref: Optional[str] = None
    newest_ingested_ref: Optional[str] = None
    cursor_version: int = Field(default=1, ge=1)


class ConnectionSurface(_StrictModel):
    """One provider-neutral connected source surface and its current projection."""

    surface_id: str = Field(min_length=1)
    category: SourceCategory
    provider: dict[str, Any] = Field(default_factory=dict)
    path: ConnectionPath
    adapter: Optional[str] = None
    acquisition_mode: Literal["snapshot", "backfill", "live"] = "snapshot"
    status: SurfaceStatus = "connected"
    privacy_scope: Literal["source", "account", "folder", "label", "workspace"] = "source"
    cursor_state: CursorState = Field(default_factory=CursorState)
    is_fixture: bool = False
    events_seen: int = Field(default=0, ge=0)
    unique_evidence_count: int = Field(default=0, ge=0)
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    gate_id: str = "usage.category.default"
    gate_version: int = Field(default=1, ge=1)
    settled_by: list[Literal["volume", "coverage"]] = Field(default_factory=list)
    settled_event_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SettlingGate(_StrictModel):
    """An immutable named/versioned settlement definition."""

    definition_identity: str = Field(min_length=1)
    gate_id: str = Field(min_length=1)
    gate_version: int = Field(ge=1)
    category: Optional[SourceCategory] = None
    min_unique_events: int = Field(default=25, ge=1)
    min_coverage_days: int = Field(default=3, ge=1)
    allow_either: bool = True
    active: bool = True


class UsageEvidence(_StrictModel):
    """Usage's current-revision index of a normalizer-owned logical identity."""

    evidence_identity: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    revision_number: int = Field(ge=1)
    source_surface_id: str = Field(min_length=1)
    source_category: SourceCategory
    connection_path: ConnectionPath
    source_ref: str = Field(min_length=1)
    provider_time: Optional[str] = None
    importer_id: str = Field(min_length=1)
    importer_version: str = Field(min_length=1)
    is_fixture: bool = False
    qualifying: bool = False
    invalidated: bool = False
    first_ingested_event_id: str = Field(min_length=1)
    last_ingested_event_id: str = Field(min_length=1)
    revision_count: int = Field(default=1, ge=1)


class SettlementRecord(_StrictModel):
    """Historical first pass for one surface and immutable gate version."""

    settlement_identity: str = Field(min_length=1)
    source_surface_id: str = Field(min_length=1)
    source_category: SourceCategory
    gate_id: str = Field(min_length=1)
    gate_version: int = Field(ge=1)
    unique_evidence_count: int = Field(ge=0)
    coverage_days: int = Field(ge=0)
    coverage_start: Optional[str] = None
    coverage_end: Optional[str] = None
    passed_thresholds: list[Literal["volume", "coverage"]]
    source_settled_event_id: str = Field(min_length=1)
    evaluated_at_event_id: str = Field(min_length=1)


class UsageRecord(_StrictModel):
    """One idempotent interaction observation attributable to a surface."""

    usage_identity: str = Field(min_length=1)
    source_surface_id: str = Field(min_length=1)
    source_category: SourceCategory
    interaction_type: str = Field(min_length=1)
    evidence_identity: Optional[str] = None
    provider_time: Optional[str] = None
    count: int = Field(default=1, ge=1)
    is_fixture: bool = False
    source_provenance: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UsageProjectionFailure(_StrictModel):
    """A fail-closed projection error with the offending event identity."""

    event_id: str = Field(min_length=1)
    source_surface_id: Optional[str] = None
    evidence_identity: Optional[str] = None
    error_code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


OBJECT_TYPES = [
    ObjectType("connection_surface", ConnectionSurface, "A provider-neutral connected source surface."),
    ObjectType("settling_gate", SettlingGate, "An immutable named and versioned settlement definition."),
    ObjectType("usage_evidence", UsageEvidence, "A projection keyed by normalizer-owned evidence identity."),
    ObjectType("settlement_record", SettlementRecord, "A surface's historical first pass of one gate version."),
    ObjectType("usage_record", UsageRecord, "An idempotent interaction observation."),
    ObjectType("usage_projection_failure", UsageProjectionFailure, "A fail-closed observational projection error."),
]


RELATION_TYPES = [
    RelationType(
        "evidence_on_surface",
        source_types=("usage_evidence",),
        target_types=("connection_surface",),
        description="A normalizer evidence identity was observed on a connection surface.",
    ),
    RelationType(
        "settlement_for",
        source_types=("settlement_record",),
        target_types=("connection_surface",),
        description="A historical settlement pass belongs to a connection surface.",
    ),
    RelationType(
        "usage_on_surface",
        source_types=("usage_record",),
        target_types=("connection_surface",),
        description="An interaction observation belongs to a connection surface.",
    ),
]


__all__ = [
    "SOURCE_CATEGORIES",
    "SourceCategory",
    "ConnectionPath",
    "SurfaceStatus",
    "CursorState",
    "ConnectionSurface",
    "SettlingGate",
    "UsageEvidence",
    "SettlementRecord",
    "UsageRecord",
    "UsageProjectionFailure",
    "OBJECT_TYPES",
    "RELATION_TYPES",
]
