"""Evidence-backed knowledge about a subject, distinct from agent identity."""

from __future__ import annotations

from typing import Any, Literal, Optional

from activegraph.packs import ObjectType, RelationType
from pydantic import BaseModel, Field


class SubjectFactVerdict(BaseModel):
    candidate_id: str
    subject_ref: str
    decision: Literal["confirm", "reject", "correct"]
    corrected_value: Optional[str] = None
    decided_by: str = "owner"
    rationale: str = ""
    status: Literal["proposed", "applied", "failed"] = "proposed"
    result_fact_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubjectFact(BaseModel):
    fact_identity: str
    subject_ref: str
    attribute: str
    value: str
    text: str
    status: Literal["promoted", "contradicted", "superseded", "forgotten"] = "promoted"
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    trust: float = Field(default=0.5, ge=0.0, le=1.0)
    candidate_id: Optional[str] = None
    annotation_id: Optional[str] = None
    evidence_id: Optional[str] = None
    source_surface_id: Optional[str] = None
    verdict_id: Optional[str] = None
    supersedes_fact_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubjectContradiction(BaseModel):
    contradiction_identity: str
    subject_ref: str
    attribute: str
    fact_ids: list[str] = Field(min_length=2)
    status: Literal["open", "resolved"] = "open"
    winning_fact_id: Optional[str] = None
    rationale: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


OBJECT_TYPES = [
    ObjectType("subject_fact_verdict", SubjectFactVerdict, "An explicit review decision over one profile candidate."),
    ObjectType("subject_fact", SubjectFact, "A promoted, subject-scoped fact with exact evidence provenance."),
    ObjectType("subject_contradiction", SubjectContradiction, "Conflicting promoted facts about one subject attribute."),
]

RELATION_TYPES = [
    RelationType("verdict_for_profile_candidate", source_types=("subject_fact_verdict",), target_types=("profile_candidate",), description="A verdict reviews a profile candidate."),
    RelationType("promoted_from_profile_candidate", source_types=("subject_fact",), target_types=("profile_candidate",), description="A subject fact was explicitly promoted from a candidate."),
    RelationType("subject_fact_grounded_in", source_types=("subject_fact",), target_types=("activity_evidence",), description="A subject fact resolves to its evidence revision."),
    RelationType("subject_fact_supersedes", source_types=("subject_fact",), target_types=("subject_fact",), description="A corrected or forgotten fact supersedes a prior fact."),
    RelationType("contradiction_involves", source_types=("subject_contradiction",), target_types=("subject_fact",), description="A contradiction references a subject fact."),
]

