"""Project candidates and promoted projects."""

from __future__ import annotations

from typing import Any, Optional

from activegraph.packs import ObjectType
from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCandidate(_StrictModel):
    """One proposed project with its explainable derivation."""

    candidate_identity: str = Field(min_length=1)
    name: str = Field(min_length=1)
    # label_seeded remains valid for replaying pre-ADR-0043 stores; new
    # derivations corroborate with labels instead of proposing from them.
    kind: str = Field(
        pattern="^(fact_seeded|synthesized|label_seeded|engagement_clustered|presence_clustered)$"
    )
    score_milli: int = Field(ge=0, le=1_000)
    sources: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    status: str = Field(
        default="proposed",
        pattern="^(proposed|confirmed|dismissed|superseded)$",
    )
    project_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Project(_StrictModel):
    """A canonical, owner-confirmed project."""

    project_identity: str = Field(min_length=1)
    name: str = Field(min_length=1)
    status: str = Field(default="active", pattern="^(active|archived|superseded)$")
    seeded_from_candidate_id: Optional[str] = None
    confirmed_by: str = Field(min_length=1)
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


OBJECT_TYPES = [
    ObjectType("project_candidate", ProjectCandidate, "A proposed project with explainable sources."),
    ObjectType("project", Project, "An owner-confirmed canonical project."),
]
RELATION_TYPES = []

__all__ = ["ProjectCandidate", "Project", "OBJECT_TYPES", "RELATION_TYPES"]
