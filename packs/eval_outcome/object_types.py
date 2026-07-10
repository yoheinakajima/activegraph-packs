"""Canonical outcome records and artifact-owned reliability projections."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from activegraph.packs import ObjectType, RelationType


OutcomeKind = Literal[
    "helped", "hurt", "neutral", "contradicted", "stale", "superseded"
]
ReliabilityVerdict = Literal["supported", "weak", "harmful", "stale"]


class ReliabilityTallies(BaseModel):
    helped: int = Field(default=0, ge=0)
    hurt: int = Field(default=0, ge=0)
    neutral: int = Field(default=0, ge=0)
    contradicted: int = Field(default=0, ge=0)
    stale: int = Field(default=0, ge=0)
    superseded: int = Field(default=0, ge=0)


class OutcomeRecord(BaseModel):
    """One canonical terminal or maintenance outcome with subject trace."""

    outcome_event_id: str
    outcome_type: OutcomeKind
    evaluation_id: Optional[str] = None
    usage_id: Optional[str] = None
    artifact_id: str
    artifact_type: str
    artifact_version: Optional[str] = None
    evidence_revision_id: Optional[str] = None
    superseding_version: Optional[str] = None
    contribution_key: str
    rationale: str = ""
    actor: str
    source_context: dict[str, Any] = Field(default_factory=dict)
    is_fixture: bool = False


class ArtifactReliability(BaseModel):
    """Current recency-aware reliability owned by one artifact version."""

    artifact_id: str
    artifact_type: str
    artifact_version: Optional[str] = None
    tallies: ReliabilityTallies = Field(default_factory=ReliabilityTallies)
    verdict: ReliabilityVerdict = "weak"
    eligible: bool = True
    retrieval_multiplier: float = Field(default=0.75, ge=0.0, le=1.0)
    latest_outcome_type: OutcomeKind
    latest_outcome_event_id: str
    latest_evaluation_id: Optional[str] = None
    latest_usage_id: Optional[str] = None
    evidence_event_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


OBJECT_TYPES = [
    ObjectType(
        "outcome_record",
        OutcomeRecord,
        "A canonical terminal or maintenance outcome with complete subject trace.",
    ),
    ObjectType(
        "artifact_reliability",
        ArtifactReliability,
        "An artifact-owned, recency-aware reliability projection.",
    ),
]


RELATION_TYPES = [
    RelationType(
        "records_evaluation",
        source_types=("outcome_record",),
        target_types=("evaluation",),
        description="A terminal or maintenance outcome traces to its Core evaluation.",
    ),
    RelationType(
        "updates_reliability",
        source_types=("outcome_record",),
        target_types=("artifact_reliability",),
        description="An outcome is evidence in one artifact reliability projection.",
    ),
]


__all__ = [
    "OutcomeRecord",
    "ArtifactReliability",
    "ReliabilityTallies",
    "OBJECT_TYPES",
    "RELATION_TYPES",
]
