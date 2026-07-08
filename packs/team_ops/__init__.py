"""activegraph.packs.team_ops — Team/Ops Pack v0.1.

Project management layer extending Core tasks with assignments, milestones, and workload.

Design: TeamTask wraps Core task via relation (does not replace it).
        Assignment links a Core task to a Principal from Identity Pack.

Object types:
  project             — Project grouping tasks and milestones
  assignment          — Task-to-principal assignment
  milestone           — Milestone grouping tasks with a target date
  workload_estimate   — Estimated workload for a person in a period
  completion_evidence — Evidence that a task was completed
  review_request      — Review request for a task or artifact

Behaviors:
  task_triager         — observation.created (task_candidate) → Core task + optional Assignment
  assignment_suggester — task.created (owner_ref set) → Assignment (if auto_assign=True)
  milestone_tracker    — milestone.created → updates open milestone registry
  completion_verifier  — observation.created (done/completed) → CompletionEvidence

Composes with: Core Pack (task), Identity Pack (principal_ref), Codebase Pack (issues→tasks)
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import TeamOpsSettings
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# requires=["core"], composes_with=["identity_auth", "codebase", "meeting"]
pack = Pack(
    name="team_ops",
    version="0.1.1",
    description=(
        "Team project management: task triage, assignment, milestone tracking, "
        "workload estimation, and completion verification. "
        "Extends Core task with 6 project management object types."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=load_prompts_from_dir(_PROMPTS_DIR) if _PROMPTS_DIR.exists() else (),
    settings_schema=TeamOpsSettings,
)

__all__ = ["pack", "TeamOpsSettings"]
