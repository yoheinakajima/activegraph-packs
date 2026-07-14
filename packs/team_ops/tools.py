"""Team/Ops Pack tools — v0.1."""

from __future__ import annotations

from activegraph import Graph
from activegraph.packs import tool


def create_project_fn(
    graph: Graph,
    name: str,
    description: str = "",
    goal: str = "",
    owner_ref: str | None = None,
    start_date: str | None = None,
    target_date: str | None = None,
) -> object:
    """Create a Project through the canonical schema (owned by the
    ``projects`` pack). PM fields live under ``metadata.ops``; the
    lifecycle stage maps onto the canonical status (planning/active/
    on_hold → active; completed/cancelled → archived)."""
    import hashlib as _hashlib

    key = " ".join(str(name).lower().split())
    identity = "project_" + _hashlib.sha256(
        ("project\x1f" + key).encode("utf-8")
    ).hexdigest()
    return graph.add_object("project", {
        "project_identity": identity,
        "name": name,
        "description": description,
        "status": "active",
        "seeded_from_candidate_id": None,
        "confirmed_by": owner_ref or "team_ops",
        "supersedes": None,
        "superseded_by": None,
        "metadata": {
            "ops": {
                "stage": "planning",
                "goal": goal,
                "owner_ref": owner_ref,
                "start_date": start_date,
                "target_date": target_date,
            },
        },
    })


def create_milestone_fn(
    graph: Graph,
    project_id: str,
    title: str,
    description: str = "",
    target_date: str | None = None,
) -> object:
    """Create a Milestone linked to a Project."""
    ms = graph.add_object("milestone", {
        "project_id": project_id,
        "title": title,
        "description": description,
        "target_date": target_date,
        "status": "upcoming",
        "task_ids": [],
        "completion_pct": 0.0,
    })
    try:
        graph.add_relation(ms.id, project_id, "part_of_project")
    except Exception:
        pass
    return ms


def submit_task_candidate_fn(
    graph: Graph,
    text: str,
    owner_ref: str | None = None,
    project_id: str | None = None,
    milestone_id: str | None = None,
    priority: str = "medium",
) -> object:
    """Create a task_candidate observation that task_triager will promote to a Core task."""
    return graph.add_object("observation", {
        "text": text,
        "confidence": 0.85,
        "category": "task_candidate",
        "metadata": {
            "task_candidate": True,
            "owner_ref": owner_ref,
            "project_id": project_id,
            "milestone_id": milestone_id,
            "priority": priority,
        },
    })


def assign_task_fn(
    graph: Graph,
    task_id: str,
    principal_ref: str,
    role: str = "assignee",
) -> object:
    """Directly create an Assignment for a task."""
    from datetime import datetime, timezone
    assignment = graph.add_object("assignment", {
        "task_id": task_id,
        "principal_ref": principal_ref,
        "role": role,
        "assigned_at": datetime.now(timezone.utc).isoformat(),
    })
    try:
        graph.add_relation(assignment.id, task_id, "assigned_to")
    except Exception:
        pass
    return assignment


def mark_task_done_fn(
    graph: Graph,
    task_id: str,
    evidence_text: str,
    completed_by_ref: str | None = None,
) -> object:
    """Create CompletionEvidence and mark the task as done."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    evidence = graph.add_object("completion_evidence", {
        "task_id": task_id,
        "evidence_text": evidence_text,
        "completed_by_ref": completed_by_ref,
        "completed_at": now,
    })
    try:
        graph.add_relation(evidence.id, task_id, "evidence_for")
    except Exception:
        pass
    try:
        graph.patch_object(task_id, {"status": "done"})
    except Exception:
        pass
    return evidence


@tool(name="create_project", description="Create a project to group tasks and milestones.")
def create_project(
    graph: Graph, name: str, description: str = "", goal: str = "",
    owner_ref: str | None = None, start_date: str | None = None, target_date: str | None = None,
) -> object:
    return create_project_fn(graph, name, description, goal, owner_ref, start_date, target_date)


@tool(name="create_milestone", description="Create a milestone within a project.")
def create_milestone(
    graph: Graph, project_id: str, title: str = "",
    description: str = "", target_date: str | None = None,
) -> object:
    return create_milestone_fn(graph, project_id, title, description, target_date)


@tool(name="submit_task_candidate", description="Submit a task candidate for triage.")
def submit_task_candidate(
    graph: Graph, text: str, owner_ref: str | None = None,
    project_id: str | None = None, milestone_id: str | None = None, priority: str = "medium",
) -> object:
    return submit_task_candidate_fn(graph, text, owner_ref, project_id, milestone_id, priority)


@tool(name="assign_task", description="Assign a task to a team member.")
def assign_task(graph: Graph, task_id: str, principal_ref: str = "", role: str = "assignee") -> object:
    return assign_task_fn(graph, task_id, principal_ref, role)


@tool(name="mark_task_done", description="Mark a task as done with completion evidence.")
def mark_task_done(
    graph: Graph, task_id: str, evidence_text: str = "", completed_by_ref: str | None = None
) -> object:
    return mark_task_done_fn(graph, task_id, evidence_text, completed_by_ref)


TOOLS = [create_project, create_milestone, submit_task_candidate, assign_task, mark_task_done]
