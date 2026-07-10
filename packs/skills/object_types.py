"""Schemas for versioned, provenance-backed learned skills."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from activegraph.packs import ObjectType, RelationType


SkillStatus = Literal["candidate", "promoted", "demoted", "disabled"]


class OutcomeSummary(BaseModel):
    helped: int = Field(default=0, ge=0)
    hurt: int = Field(default=0, ge=0)
    neutral: int = Field(default=0, ge=0)


class SkillArtifact(BaseModel):
    """One immutable definition version plus reversible lifecycle state."""

    skill_id: str
    version_identity: str
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
    definition_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    description: str = ""
    trigger_conditions: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    contraindications: list[str] = Field(default_factory=list)
    source_evidence_refs: list[str] = Field(default_factory=list)
    source_candidate_id: Optional[str] = None
    status: SkillStatus = "candidate"
    outcome_summary: OutcomeSummary = Field(default_factory=OutcomeSummary)
    promotion_history: list[dict[str, Any]] = Field(default_factory=list)
    definition_locked: bool = False
    demotion_reason: Optional[str] = None
    is_fixture: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillUsage(BaseModel):
    """One idempotent invocation of an exact skill version."""

    usage_id: str
    skill_id: str
    skill_version: str
    skill_version_id: str
    execution_ref: str
    execution_kind: Literal["real", "trial"]
    actor: str
    source_context: dict[str, Any] = Field(default_factory=dict)
    required_capabilities: list[str] = Field(default_factory=list)
    capability_call_ids: list[str] = Field(default_factory=list)
    used_event_id: str
    is_fixture: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillPromotionEvidence(BaseModel):
    """Recorded proof considered by the promotion gate."""

    evidence_id: str
    skill_version_id: str
    kind: Literal[
        "trial",
        "replay",
        "repeated_accepted_use",
        "verification",
        "task_completion",
        "owner_approval",
    ]
    reference_ids: list[str] = Field(min_length=1)
    accepted: bool = True
    rationale: str = ""
    actor: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillEvaluationLink(BaseModel):
    """Connects a usage to the Core evaluation that judged it."""

    usage_id: str
    skill_version_id: str
    evaluation_id: str
    evaluated_event_id: str


OBJECT_TYPES = [
    ObjectType("skill", SkillArtifact, "A governed immutable learned-skill version."),
    ObjectType("skill_usage", SkillUsage, "One idempotent exact-version invocation."),
    ObjectType(
        "skill_promotion_evidence",
        SkillPromotionEvidence,
        "Recorded trial, verification, accepted-use, task, or owner proof.",
    ),
    ObjectType(
        "skill_evaluation_link",
        SkillEvaluationLink,
        "A graph-visible link from one usage to its Core evaluation.",
    ),
]


RELATION_TYPES = [
    RelationType(
        "usage_of",
        source_types=("skill_usage",),
        target_types=("skill",),
        description="A usage invokes one exact skill version.",
    ),
    RelationType(
        "promotion_for",
        source_types=("skill_promotion_evidence",),
        target_types=("skill",),
        description="Promotion proof supports one exact skill version.",
    ),
    RelationType(
        "evaluation_of_usage",
        source_types=("skill_evaluation_link",),
        target_types=("skill_usage",),
        description="A link identifies the usage that was evaluated.",
    ),
    RelationType(
        "links_evaluation",
        source_types=("skill_evaluation_link",),
        target_types=("evaluation",),
        description="A skill evaluation link names its Core evaluation.",
    ),
]


__all__ = [
    "SkillArtifact",
    "SkillUsage",
    "SkillPromotionEvidence",
    "SkillEvaluationLink",
    "OBJECT_TYPES",
    "RELATION_TYPES",
]
