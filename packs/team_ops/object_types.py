"""Team/Ops Pack object and relation types — v0.1.

Extends Core task with project management primitives.
TeamTask wraps a Core task via relation (does not replace it).
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from activegraph.packs import ObjectType, RelationType


# The canonical ``project`` ObjectType is owned by the ``projects`` pack
# (one global type name, one schema — never two). team_ops REQUIRES the
# projects pack and adapts its PM fields (stage/goal/dates/owner) into
# ``metadata.ops`` on the canonical object; a load-time migration behavior
# upgrades rows minted by team_ops ≤0.1 to the canonical shape.


class Assignment(BaseModel):
    """Links a Core task or TeamTask to a principal (person)."""
    task_id: str = Field(description="ID of the Core task being assigned.")
    principal_ref: str = Field(description="Who the task is assigned to.")
    role: str = Field(default="assignee", description="E.g. 'assignee', 'reviewer', 'approver'.")
    assigned_at: Optional[str] = Field(default=None)
    workload_estimate_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Milestone(BaseModel):
    project_id: str
    title: str
    description: str = Field(default="")
    target_date: Optional[str] = Field(default=None)
    status: Literal["upcoming", "in_progress", "completed", "missed"] = Field(default="upcoming")
    task_ids: list[str] = Field(default_factory=list, description="Core task IDs in this milestone.")
    completion_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkloadEstimate(BaseModel):
    principal_ref: str
    period: str = Field(default="", description="E.g. 'week-2026-W23'.")
    estimated_hours: float = Field(default=0.0)
    committed_hours: float = Field(default=0.0)
    capacity_hours: float = Field(default=40.0)
    overloaded: bool = Field(default=False)
    task_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompletionEvidence(BaseModel):
    task_id: str
    evidence_text: str = Field(description="Description of how the task was completed.")
    completed_by_ref: Optional[str] = Field(default=None)
    completed_at: Optional[str] = Field(default=None)
    source_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewRequest(BaseModel):
    task_id: str
    requested_by_ref: str
    reviewer_ref: str
    review_type: Literal["code_review", "design_review", "content_review", "approval"] = Field(default="approval")
    status: Literal["pending", "in_review", "approved", "rejected", "cancelled"] = Field(default="pending")
    notes: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


OBJECT_TYPES = [
    ObjectType(name="assignment", schema=Assignment,
               description="Assignment of a task to a principal."),
    ObjectType(name="milestone", schema=Milestone,
               description="A milestone grouping tasks with a target date."),
    ObjectType(name="workload_estimate", schema=WorkloadEstimate,
               description="Estimated workload for a principal in a period."),
    ObjectType(name="completion_evidence", schema=CompletionEvidence,
               description="Evidence that a task was completed."),
    ObjectType(name="review_request", schema=ReviewRequest,
               description="A request for review of a task or artifact."),
]

RELATION_TYPES = [
    RelationType(name="assigned_to", source_types=("assignment",), target_types=("task",),
                 description="Assignment links to the Core task."),
    RelationType(name="depends_on", source_types=("task",), target_types=("task",),
                 description="Task depends on another task."),
    RelationType(name="part_of_milestone", source_types=("task",), target_types=("milestone",),
                 description="Task is part of a Milestone."),
    RelationType(name="part_of_project", source_types=("task", "milestone"),
                 target_types=("project",),
                 description="Task or Milestone is part of a Project."),
    RelationType(name="evidence_for", source_types=("completion_evidence",), target_types=("task",),
                 description="Evidence for task completion."),
    RelationType(name="review_of", source_types=("review_request",), target_types=("task",),
                 description="ReviewRequest is for a task."),
    RelationType(name="workload_for", source_types=("workload_estimate",), target_types=("assignment",),
                 description="WorkloadEstimate associated with an Assignment."),
]
