"""Semantic observations and inspectable learned importance/trust vectors."""

from __future__ import annotations

from typing import Any, Literal, Optional

from activegraph.packs import ObjectType
from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


SignalType = Literal[
    "impression",
    "opened",
    "active_dwell",
    "revisited",
    "created",
    "edited",
    "replied",
    "completed",
    "dismissed",
    "archived",
    "explicit_important",
    "explicit_not_important",
    "nonresponse_window",
    "llm_judgment",
]


class AttentionObservation(_StrictModel):
    """One semantic user/system signal, never raw UI telemetry."""

    observation_id: str = Field(min_length=1)
    subject_ref: str = Field(min_length=1)
    subject_kind: str = Field(default="object", min_length=1)
    signal_type: SignalType
    strength_milli: int = Field(default=1_000, ge=0, le=1_000)
    context_key: str = Field(default="global", min_length=1)
    objective_ref: Optional[str] = None
    horizon_key: str = Field(default="current", min_length=1)
    session_id: Optional[str] = None
    opportunity_id: Optional[str] = Field(
        default=None,
        description=(
            "Exposure/opportunity identity. Required before absence such as "
            "nonresponse_window may count as negative evidence."
        ),
    )
    active_ms: Optional[int] = Field(default=None, ge=0)
    occurred_at: Optional[str] = None
    source: Literal["client", "connector", "user", "agent", "importer"] = "client"
    explicit: bool = False
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class InteractionBatch(_StrictModel):
    """A bounded flush of semantic observations from one client session."""

    batch_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    batch_sequence: int = Field(ge=0)
    client_id: str = Field(min_length=1)
    client_version: str = ""
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    active_duration_ms: int = Field(default=0, ge=0)
    observation_ids: list[str] = Field(default_factory=list)
    raw_event_count: int = Field(default=0, ge=0)
    flush_reason: str = "manual"
    privacy_mode: Literal["semantic_only"] = "semantic_only"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ImportanceVector(_StrictModel):
    """Current subject-scoped salience prediction and its complete evidence."""

    vector_key: str = Field(min_length=1)
    subject_ref: str = Field(min_length=1)
    subject_kind: str = Field(default="object", min_length=1)
    context_key: str = Field(default="global", min_length=1)
    objective_ref: Optional[str] = None
    horizon_key: str = Field(default="current", min_length=1)
    score_milli: int = Field(ge=0, le=1_000)
    confidence_milli: int = Field(ge=0, le=1_000)
    priority_band: Literal["unranked", "low", "medium", "high"]
    support_milli: int = Field(default=0, ge=0)
    oppose_milli: int = Field(default=0, ge=0)
    features: dict[str, int] = Field(default_factory=dict)
    observation_ids: list[str] = Field(default_factory=list)
    policy_id: str = Field(min_length=1)
    policy_version: int = Field(ge=1)
    latest_observation_id: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceTrustVector(_StrictModel):
    """Domain-scoped credibility prediction learned from canonical outcomes."""

    vector_key: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    source_kind: str = Field(default="source", min_length=1)
    domain: str = Field(default="general", min_length=1)
    query_scope: str = Field(default="general", min_length=1)
    score_milli: int = Field(ge=0, le=1_000)
    confidence_milli: int = Field(ge=0, le=1_000)
    verdict: Literal["unproven", "weak", "supported", "harmful"]
    support_milli: int = Field(default=0, ge=0)
    challenge_milli: int = Field(default=0, ge=0)
    features: dict[str, int] = Field(default_factory=dict)
    outcome_event_ids: list[str] = Field(default_factory=list)
    policy_id: str = Field(min_length=1)
    policy_version: int = Field(ge=1)
    latest_outcome_event_id: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


OBJECT_TYPES = [
    ObjectType(
        "attention_observation",
        AttentionObservation,
        "A semantic engagement/outcome signal about a subject.",
    ),
    ObjectType(
        "interaction_batch",
        InteractionBatch,
        "A bounded semantic-only flush from one client session.",
    ),
    ObjectType(
        "importance_vector",
        ImportanceVector,
        "An inspectable context-scoped prediction of subject salience.",
    ),
    ObjectType(
        "source_trust_vector",
        SourceTrustVector,
        "An inspectable domain-scoped prediction of source credibility.",
    ),
]

RELATION_TYPES = []

__all__ = [
    "AttentionObservation",
    "InteractionBatch",
    "ImportanceVector",
    "SourceTrustVector",
    "OBJECT_TYPES",
    "RELATION_TYPES",
]
