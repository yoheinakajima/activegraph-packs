"""Provider-neutral ingestion, evidence, replay, and candidate schemas.

The importer-facing ``acquired_item`` is intentionally strict and small.  All
category/path/content handoff data lives in ``acquired_content`` so importers
cannot accidentally grow the stable acquisition contract.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Optional

from activegraph.packs import ObjectType, RelationType
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ReplayMode = Literal["inline", "artifact", "reference_only"]
SourceCategory = Literal[
    "communication",
    "rhythm",
    "ai_activity",
    "code_work",
    "local_knowledge",
    "tool_automation",
    "outcome_evaluation",
]
# "manual" is the paste-back transport (ADR 0025): owner-pasted content a
# connected assistant could equally push over "mcp" — one surface, the
# transport is connection-path metadata, and evidence identity is unchanged.
ConnectionPath = Literal["export", "mcp", "composio", "native", "local", "pack", "manual"]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AcquiredItem(_StrictModel):
    """One fully identified provider/file/export unit emitted by an importer."""

    source_surface_id: str = Field(min_length=1)
    provider_item_id: Optional[str] = None
    dedup_key: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    source_hash: Optional[str] = None
    provider_time: Optional[str] = None
    replay_mode: ReplayMode
    replay_payload_ref: str
    replay_payload_hash: str
    media_type: str = Field(min_length=1)
    importer_id: str = Field(min_length=1)
    importer_version: str = Field(min_length=1)

    @field_validator("source_hash", "replay_payload_hash")
    @classmethod
    def _valid_sha256(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _SHA256_RE.fullmatch(value):
            raise ValueError("must be a lowercase 64-character SHA-256 hex digest")
        return value

    @model_validator(mode="after")
    def _payload_reference_matches_mode(self) -> "AcquiredItem":
        if self.replay_mode in ("inline", "artifact") and not self.replay_payload_ref:
            raise ValueError(f"{self.replay_mode} replay requires replay_payload_ref")
        return self


class AcquiredContent(_StrictModel):
    """The exact normalized-content handoff paired with an acquired item."""

    acquired_item_id: str = Field(min_length=1)
    normalized_content: str
    normalized_metadata: dict[str, Any]
    source_category: SourceCategory
    connection_path: ConnectionPath
    is_fixture: bool


class BackfillCursor(_StrictModel):
    """Stable bidirectional progress for snapshots, backfills, and live feeds."""

    source_surface_id: str = Field(min_length=1)
    oldest_ingested_ref: Optional[str] = None
    newest_ingested_ref: Optional[str] = None
    watermark_ref: Optional[str] = Field(
        default=None,
        description="Provider-stable live checkpoint such as a Gmail history id.",
    )
    cursor_version: int = Field(default=1, ge=1)


class EvidenceInvalidationRequest(_StrictModel):
    """Provider tombstone handoff; the normalizer owns evidence invalidation."""

    request_identity: str = Field(min_length=1)
    source_surface_id: str = Field(min_length=1)
    provider_item_id: Optional[str] = None
    evidence_identity: Optional[str] = None
    reason: Literal["provider_deleted", "source_revoked", "owner_delete_by_source"]
    status: Literal["proposed", "fulfilled", "failed"] = "proposed"
    invalidated_evidence_ids: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _has_identity(self) -> "EvidenceInvalidationRequest":
        if not self.provider_item_id and not self.evidence_identity:
            raise ValueError("provider_item_id or evidence_identity is required")
        return self


class ActivityEvidence(_StrictModel):
    """One immutable revision of a logical, provider-neutral evidence identity."""

    evidence_identity: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    revision_number: int = Field(ge=1)
    status: Literal["current", "superseded", "stale", "revoked"] = "current"
    acquired_item_id: str
    acquired_content_id: str
    source_surface_id: str
    provider_item_id: Optional[str] = None
    dedup_key: str
    source_ref: str
    source_hash: Optional[str] = None
    content_hash: str
    provider_time: Optional[str] = None
    replay_mode: ReplayMode
    replay_payload_ref: str
    replay_payload_hash: str
    replay_complete: bool
    media_type: str
    encoding: str = "utf-8"
    retention_policy: str = "source_default"
    acquired_at_event_id: str
    normalized_content: str
    normalized_metadata: dict[str, Any] = Field(default_factory=dict)
    source_category: SourceCategory
    connection_path: ConnectionPath
    importer_id: str
    importer_version: str
    is_fixture: bool = False
    supersedes_evidence_id: Optional[str] = None

    @field_validator("source_hash", "content_hash", "replay_payload_hash")
    @classmethod
    def _valid_evidence_hash(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _SHA256_RE.fullmatch(value):
            raise ValueError("must be a lowercase 64-character SHA-256 hex digest")
        return value


class IngestionFailure(_StrictModel):
    """A recorded, non-partial failure at one ingestion pipeline stage."""

    source_surface_id: Optional[str] = None
    acquired_item_id: Optional[str] = None
    source_ref: Optional[str] = None
    stage: Literal["acquisition", "normalization", "replay", "extraction", "cursor"]
    error_code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    importer_id: Optional[str] = None
    importer_version: Optional[str] = None
    extractor_id: Optional[str] = None
    extractor_version: Optional[str] = None
    recoverable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractionRecord(_StrictModel):
    """One idempotent extractor/configuration run over one evidence revision."""

    extraction_identity: str
    evidence_id: str
    evidence_identity: str
    revision_id: str
    extractor_id: str
    extractor_version: str
    extraction_config_id: str
    input_content_hash: str
    input_replay_payload_hash: str
    candidate_ids: list[str] = Field(default_factory=list)
    candidate_types: list[str] = Field(default_factory=list)
    status: Literal["completed", "invalidated", "failed"] = "completed"
    replayed: bool = False
    replay_verified: bool = False
    error: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("input_content_hash", "input_replay_payload_hash")
    @classmethod
    def _valid_input_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("must be a lowercase 64-character SHA-256 hex digest")
        return value


class ExtractorVersionState(_StrictModel):
    """Current eligibility state for an extractor version/configuration."""

    state_identity: str
    extractor_id: str
    extractor_version: str
    extraction_config_id: Optional[str] = None
    status: Literal["enabled", "disabled"] = "enabled"
    reason: str = ""
    changed_at_event_id: Optional[str] = None


class _CandidateBase(_StrictModel):
    candidate_identity: str
    text: str = Field(min_length=1)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    evidence_id: str
    evidence_identity: str
    revision_id: str
    extraction_record_id: str
    extractor_id: str
    extractor_version: str
    extraction_config_id: str
    status: Literal["candidate", "invalidated"] = "candidate"
    invalidation_reason: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PreferenceCandidate(_CandidateBase):
    preference: str


class TaskCandidate(_CandidateBase):
    title: str
    description: str = ""


class ProfileCandidate(_CandidateBase):
    attribute: str = "profile_statement"
    value: str


class SkillCandidate(_CandidateBase):
    name: str
    description: str = ""


class EvalCandidate(_CandidateBase):
    subject: str = "source_activity"
    judgment: str


OBJECT_TYPES = [
    ObjectType("acquired_item", AcquiredItem, "A strict importer-emitted acquisition unit."),
    ObjectType("acquired_content", AcquiredContent, "Normalized content paired with an acquired item."),
    ObjectType("backfill_cursor", BackfillCursor, "Stable source backfill progress without offsets."),
    ObjectType(
        "evidence_invalidation_request",
        EvidenceInvalidationRequest,
        "A provider/owner tombstone fulfilled by the identity-owning normalizer.",
    ),
    ObjectType("activity_evidence", ActivityEvidence, "A provider-neutral evidence revision."),
    ObjectType("ingestion_failure", IngestionFailure, "A recorded ingestion pipeline failure."),
    ObjectType("extraction_record", ExtractionRecord, "A versioned deterministic extraction run."),
    ObjectType("extractor_version_state", ExtractorVersionState, "Eligibility of an extractor version."),
    ObjectType("preference_candidate", PreferenceCandidate, "A preference proposed from evidence."),
    ObjectType("task_candidate", TaskCandidate, "A task proposed from evidence."),
    ObjectType("profile_candidate", ProfileCandidate, "A profile fact proposed from evidence."),
    ObjectType("skill_candidate", SkillCandidate, "A learned behavior proposed from evidence."),
    ObjectType("eval_candidate", EvalCandidate, "An evaluation signal proposed from evidence."),
]


_CANDIDATE_TYPES = (
    "memory_candidate",
    "preference_candidate",
    "task_candidate",
    "profile_candidate",
    "skill_candidate",
    "eval_candidate",
)

RELATION_TYPES = [
    RelationType(
        "content_for",
        source_types=("acquired_content",),
        target_types=("acquired_item",),
        description="Normalized content belongs to one acquired item.",
    ),
    RelationType(
        "normalizes_to",
        source_types=("acquired_content",),
        target_types=("activity_evidence",),
        description="An acquired-content handoff normalizes into evidence.",
    ),
    RelationType(
        "acquired_from",
        source_types=("activity_evidence",),
        target_types=("acquired_item",),
        description="Evidence preserves its exact acquisition provenance.",
    ),
    RelationType(
        "supersedes",
        source_types=("activity_evidence",),
        target_types=("activity_evidence",),
        description="A new evidence revision supersedes an older revision.",
    ),
    RelationType(
        "extraction_for",
        source_types=("extraction_record",),
        target_types=("activity_evidence",),
        description="An extraction record evaluates one evidence revision.",
    ),
    RelationType(
        "produced_candidate",
        source_types=("extraction_record",),
        target_types=_CANDIDATE_TYPES,
        description="A versioned extraction produced a candidate.",
    ),
    RelationType(
        "extracted_from",
        source_types=_CANDIDATE_TYPES,
        target_types=("activity_evidence",),
        description="A candidate traces directly to its evidence revision.",
    ),
    RelationType(
        "failure_for",
        source_types=("ingestion_failure",),
        target_types=("acquired_item", "acquired_content", "activity_evidence"),
        description="A failure identifies the pipeline object it affected.",
    ),
]


__all__ = [
    "AcquiredItem",
    "AcquiredContent",
    "BackfillCursor",
    "EvidenceInvalidationRequest",
    "ActivityEvidence",
    "IngestionFailure",
    "ExtractionRecord",
    "ExtractorVersionState",
    "PreferenceCandidate",
    "TaskCandidate",
    "ProfileCandidate",
    "SkillCandidate",
    "EvalCandidate",
    "ReplayMode",
    "SourceCategory",
    "ConnectionPath",
    "OBJECT_TYPES",
    "RELATION_TYPES",
]
