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
    """Create a Project through the canonical pipeline: the ``projects``
    pack owns the type AND the mint (D062 — one mint site per canonical
    artifact). team_ops keeps its PM-flavored signature; the record lands
    identically either door."""
    from packs.projects.tools import create_workstream_fn

    result = create_workstream_fn(
        graph, name, description=description, goal=goal,
        start_date=start_date, target_date=target_date,
        owner_ref=owner_ref, actor=owner_ref or "team_ops",
    )
    project = graph.get_object(result["project_id"])
    if not result.get("already_exists"):
        # The PM lifecycle shape team_ops consumers rely on, adapted onto
        # the canonical record — never a second store.
        metadata = dict((project.data or {}).get("metadata") or {})
        metadata["ops"] = {
            "stage": "planning",
            "goal": goal,
            "owner_ref": owner_ref,
            "start_date": start_date,
            "target_date": target_date,
        }
        graph.patch_object(
            project.id, {"metadata": metadata},
            rationale="team_ops PM fields adapted onto the canonical project",
        )
        project = graph.get_object(project.id)
    return project


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


def create_task_fn(
    graph: Graph,
    title: str,
    description: str = "",
    project_id: str | None = None,
    milestone_id: str | None = None,
    priority: str = "medium",
    due_at: str | None = None,
    owner_ref: str | None = None,
    source_refs: list[str] | None = None,
) -> object:
    """Owner-authored canonical task: born ``active`` (the owner asked for
    it — no candidate ceremony), scoped to its workstream through
    ``part_of_project``, with the deadline on the Core field."""
    title = " ".join(str(title).split())
    if not title:
        raise ValueError("a task needs a title")
    task = graph.add_object("task", {
        "title": title[:80],
        "description": description or title,
        "status": "active",
        "priority": priority if priority in ("low", "medium", "high", "critical") else "medium",
        "owner_ref": owner_ref,
        "due_at": due_at,
        "source_observation_ids": [str(r) for r in (source_refs or [])],
    })
    if project_id:
        graph.add_relation(task.id, project_id, "part_of_project")
    if milestone_id:
        graph.add_relation(task.id, milestone_id, "part_of_milestone")
    return task


def accept_task_candidate_fn(
    graph: Graph,
    candidate_ref: str,
    project_id: str | None = None,
    priority: str | None = None,
    due_at: str | None = None,
    owner_ref: str | None = None,
) -> object:
    """Adapt an evidence-backed ``task_candidate`` (activity_normalizer)
    into a canonical Core task at the moment the owner accepts it — the
    seam between the extraction stream and team_ops triage. The candidate
    object stays untouched (its lifecycle belongs to its own pack); the
    task carries the candidate and its evidence as provenance. Idempotent
    per candidate."""
    candidate = graph.get_object(candidate_ref)
    if candidate is None or candidate.type != "task_candidate":
        raise ValueError(f"unknown task candidate {candidate_ref!r}")
    for existing in graph.objects(type="task"):
        if candidate_ref in (existing.data.get("source_observation_ids") or []):
            return existing
    data = candidate.data or {}
    text = str(data.get("title") or data.get("text") or "")
    task = graph.add_object("task", {
        "title": text[:80] or candidate_ref,
        "description": str(data.get("description") or data.get("text") or text),
        "status": "active",
        "priority": priority if priority in ("low", "medium", "high", "critical") else "medium",
        "owner_ref": owner_ref,
        "due_at": due_at,
        "source_observation_ids": [
            candidate_ref,
            *(str(r) for r in (data.get("evidence_id"),) if r),
        ],
    })
    if project_id:
        graph.add_relation(task.id, project_id, "part_of_project")
    return task


def project_tasks_fn(reader, project_id: str | None = None) -> dict:
    """Neutral task listing with the workstream join: every canonical task,
    its project (via ``part_of_project``), due, priority, and status —
    the read any host's task surfaces should share."""
    project_by_task: dict[str, str] = {}
    for relation in reader.relations(type="part_of_project"):
        if (relation.data or {}).get("removed"):
            continue
        project_by_task.setdefault(relation.source, relation.target)
    rows = []
    for task in reader.objects(type="task"):
        data = task.data or {}
        owning_project = project_by_task.get(task.id)
        if project_id and owning_project != project_id:
            continue
        rows.append({
            "task_id": task.id,
            "title": data.get("title"),
            "status": data.get("status"),
            "priority": data.get("priority"),
            "due_at": data.get("due_at"),
            "owner_ref": data.get("owner_ref"),
            "project_id": owning_project,
            "source_refs": list(data.get("source_observation_ids") or []),
        })
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    rows.sort(key=lambda row: (
        row["status"] != "active",
        order.get(str(row["priority"]), 2),
        str(row["due_at"] or "9999"),
        str(row["task_id"]),
    ))
    return {"tasks": rows}


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


@tool(name="create_task", description="Owner-authored canonical task, active immediately, scoped to its workstream.")
def create_task(
    graph: Graph, title: str, description: str = "", project_id: str | None = None,
    priority: str = "medium", due_at: str | None = None, owner_ref: str | None = None,
) -> object:
    return create_task_fn(
        graph, title, description=description, project_id=project_id,
        priority=priority, due_at=due_at, owner_ref=owner_ref,
    )


@tool(name="accept_task_candidate", description="Adopt an evidence-backed task candidate as a canonical task with provenance.")
def accept_task_candidate(
    graph: Graph, candidate_ref: str, project_id: str | None = None,
    priority: str | None = None, due_at: str | None = None, owner_ref: str | None = None,
) -> object:
    return accept_task_candidate_fn(
        graph, candidate_ref, project_id=project_id, priority=priority,
        due_at=due_at, owner_ref=owner_ref,
    )


TOOLS = [
    create_project, create_milestone, submit_task_candidate, assign_task,
    mark_task_done, create_task, accept_task_candidate,
]
