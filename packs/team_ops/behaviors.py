"""Team/Ops Pack behaviors — v0.1.

Behaviors:
  task_triager          — observation.created (task_candidate hint) → Core task + Assignment
  assignment_suggester  — task.created → Assignment (if auto_assign_tasks=True)
  milestone_tracker     — task.created → links task to open milestone if title matches
  completion_verifier   — observation.created (done/completed category) → CompletionEvidence

Design: TeamTask wraps Core task via relations, not a separate object type.
        Assignment links a Core task to a Principal from Identity Pack.

Registries:
  _PROJECT_MILESTONE_REGISTRY: project_id → [milestone_id, ...]
  _TASK_ASSIGNMENTS: task_id → assignment_id
  Call clear_team_ops_registry() between test fixtures.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from activegraph.packs import behavior

from .settings import TeamOpsSettings

_PROJECT_MILESTONE_REGISTRY: dict[str, list] = {}
_TASK_ASSIGNMENTS: dict[str, str] = {}
_OPEN_MILESTONES: dict[str, dict] = {}


def clear_team_ops_registry() -> None:
    _PROJECT_MILESTONE_REGISTRY.clear()
    _TASK_ASSIGNMENTS.clear()
    _OPEN_MILESTONES.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _word_overlap(a: str, b: str) -> float:
    STOPWORDS = {"a", "an", "the", "and", "or", "in", "on", "to", "for", "of", "is", "be"}
    wa = {w for w in re.findall(r"[a-z]+", a.lower()) if w not in STOPWORDS and len(w) > 2}
    wb = {w for w in re.findall(r"[a-z]+", b.lower()) if w not in STOPWORDS and len(w) > 2}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _extract_owner_from_text(text: str) -> Optional[str]:
    match = re.search(r"(?:assign(?:ed)? to|owner:|@)([A-Za-z0-9._+@-]+)", text, re.I)
    if match:
        return match.group(1).strip()
    return None


@behavior(
    name="task_triager",
    on=["object.created"],
    where={"object.type": "observation"},
    creates=["task", "assignment"],
)
def task_triager(event, graph, ctx, *, settings: TeamOpsSettings):
    """Promote task_candidate observations to Core tasks.

    On: object.created (observation, category='task_candidate' or metadata.task_candidate=True)
    Creates: task (Core), optionally assignment
    Relations: part_of_project (if project_id in metadata)

    The task_candidate hint comes from other pack behaviors or direct user input.
    """
    obj = event.payload.get("object", {})
    obs_id = obj.get("id")
    data = obj.get("data", {})

    category = data.get("category") or ""
    meta = data.get("metadata") or {}
    is_task_candidate = (
        category == settings.task_candidate_hint
        or meta.get("task_candidate") is True
        or "action_item" == category
    )

    if not is_task_candidate:
        return

    text = data.get("text") or ""
    owner_ref = _extract_owner_from_text(text) or meta.get("owner_ref")
    project_id = meta.get("project_id")
    milestone_id = meta.get("milestone_id")
    priority = meta.get("priority") or "medium"

    try:
        task = graph.add_object("task", {
            "title": text[:80],
            "description": text,
            "status": "candidate",
            "priority": priority,
            "owner_ref": owner_ref,
            "source_observation_ids": [obs_id],
        })
        task_id = task.id

        if project_id:
            try:
                graph.add_relation(task_id, project_id, "part_of_project")
            except Exception:
                pass

        if milestone_id:
            try:
                graph.add_relation(task_id, milestone_id, "part_of_milestone")
            except Exception:
                pass

        if owner_ref and settings.auto_assign_tasks:
            try:
                assignment = graph.add_object("assignment", {
                    "task_id": task_id,
                    "principal_ref": owner_ref,
                    "role": "assignee",
                    "assigned_at": _now_iso(),
                })
                graph.add_relation(assignment.id, task_id, "assigned_to")
                _TASK_ASSIGNMENTS[task_id] = assignment.id
            except Exception:
                pass

    except Exception:
        pass


@behavior(
    name="assignment_suggester",
    on=["object.created"],
    where={"object.type": "task"},
    creates=["assignment"],
)
def assignment_suggester(event, graph, ctx, *, settings: TeamOpsSettings):
    """Create an Assignment for newly created tasks when auto_assign is enabled.

    On: object.created (task, owner_ref is set)
    Creates: assignment
    Relations: assigned_to(assignment → task)

    v0.1: assigns to task.owner_ref if present.
    LLM-powered workload-aware assignment in v0.2.
    """
    if not settings.auto_assign_tasks:
        return

    obj = event.payload.get("object", {})
    task_id = obj.get("id")
    data = obj.get("data", {})

    if task_id in _TASK_ASSIGNMENTS:
        return

    owner_ref = data.get("owner_ref")
    if not owner_ref:
        return

    priority = data.get("priority") or "medium"
    review_needed = (
        settings.require_review_for_critical_tasks and priority == "critical"
    )

    try:
        assignment = graph.add_object("assignment", {
            "task_id": task_id,
            "principal_ref": owner_ref,
            "role": "assignee",
            "assigned_at": _now_iso(),
        })
        graph.add_relation(assignment.id, task_id, "assigned_to")
        _TASK_ASSIGNMENTS[task_id] = assignment.id

        if review_needed:
            try:
                graph.add_object("review_request", {
                    "task_id": task_id,
                    "requested_by_ref": "system",
                    "reviewer_ref": owner_ref,
                    "review_type": "approval",
                    "status": "pending",
                    "notes": "Auto-created for critical priority task.",
                })
            except Exception:
                pass

    except Exception:
        pass


@behavior(
    name="milestone_tracker",
    on=["object.created"],
    where={"object.type": "milestone"},
    creates=[],
)
def milestone_tracker(event, graph, ctx, *, settings: TeamOpsSettings):
    """Track open milestones for efficient task linking.

    On: object.created (milestone)
    Updates: _OPEN_MILESTONES registry for use by task_triager

    This behavior maintains the module-level registry so task_triager can
    link tasks to milestones without calling graph.objects().
    """
    obj = event.payload.get("object", {})
    milestone_id = obj.get("id")
    data = obj.get("data", {})

    project_id = data.get("project_id") or ""
    title = data.get("title") or ""
    status = data.get("status") or "upcoming"

    if status in ("upcoming", "in_progress"):
        _OPEN_MILESTONES[milestone_id] = {
            "project_id": project_id,
            "title": title,
            "status": status,
        }
        if project_id:
            if project_id not in _PROJECT_MILESTONE_REGISTRY:
                _PROJECT_MILESTONE_REGISTRY[project_id] = []
            if milestone_id not in _PROJECT_MILESTONE_REGISTRY[project_id]:
                _PROJECT_MILESTONE_REGISTRY[project_id].append(milestone_id)


@behavior(
    name="completion_verifier",
    on=["object.created"],
    where={"object.type": "observation"},
    creates=["completion_evidence"],
)
def completion_verifier(event, graph, ctx, *, settings: TeamOpsSettings):
    """Create CompletionEvidence when a 'done' observation references a task.

    On: object.created (observation, category=decision or fact, text mentions 'done'/'completed')
    Creates: completion_evidence
    Relations: evidence_for(evidence → task)

    Pattern: looks for 'completed', 'done', 'finished' + task reference in observation.
    """
    obj = event.payload.get("object", {})
    obs_id = obj.get("id")
    data = obj.get("data", {})

    category = data.get("category") or ""
    if category not in ("decision", "fact"):
        return

    text = (data.get("text") or "").lower()
    if not any(w in text for w in ("completed", "done", "finished", "closed", "resolved")):
        return

    meta = data.get("metadata") or {}
    task_id = meta.get("task_id")
    if not task_id:
        return

    completed_by = meta.get("completed_by_ref") or data.get("metadata", {}).get("sender_ref")
    source_ids = data.get("source_ids") or []

    try:
        evidence = graph.add_object("completion_evidence", {
            "task_id": task_id,
            "evidence_text": data.get("text") or "",
            "completed_by_ref": completed_by,
            "completed_at": _now_iso(),
            "source_ids": source_ids,
        })
        graph.add_relation(evidence.id, task_id, "evidence_for")

        try:
            graph.patch_object(task_id, {"status": "done"})
        except Exception:
            pass

    except Exception:
        pass


BEHAVIORS = [
    task_triager,
    assignment_suggester,
    milestone_tracker,
    completion_verifier,
]
