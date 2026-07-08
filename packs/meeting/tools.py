"""Meeting Pack tools — v0.1."""

from __future__ import annotations

from activegraph import Graph
from activegraph.packs import tool


def ingest_transcript_fn(
    graph: Graph,
    title: str,
    content: str,
    date: str | None = None,
    participants: list[str] | None = None,
    platform: str = "other",
    duration_minutes: int | None = None,
) -> object:
    """Create a meeting_transcript source, triggering transcript_ingester."""
    return graph.add_object("source", {
        "kind": "meeting_transcript",
        "content": content,
        "channel": "meeting",
        "metadata": {
            "title": title,
            "date": date,
            "participants": participants or [],
            "platform": platform,
            "duration_minutes": duration_minutes,
        },
    })


def create_meeting_fn(
    graph: Graph,
    title: str,
    date: str | None = None,
    participants: list[str] | None = None,
    platform: str = "other",
    duration_minutes: int | None = None,
) -> object:
    """Directly create a Meeting object (without transcript)."""
    return graph.add_object("meeting", {
        "title": title,
        "date": date,
        "duration_minutes": duration_minutes,
        "platform": platform,
        "participants": participants or [],
        "status": "completed",
    })


def add_decision_fn(
    graph: Graph,
    meeting_id: str,
    text: str,
    decided_by: list[str] | None = None,
    confidence: float = 0.85,
) -> object:
    """Manually add a MeetingDecision to an existing meeting."""
    decision = graph.add_object("meeting_decision", {
        "meeting_id": meeting_id,
        "text": text,
        "decided_by": decided_by or [],
        "confidence": confidence,
    })
    try:
        graph.add_relation(decision.id, meeting_id, "decision_in")
    except Exception:
        pass
    return decision


def add_action_item_fn(
    graph: Graph,
    meeting_id: str,
    text: str,
    owner_ref: str | None = None,
    due_at: str | None = None,
    create_task: bool = True,
) -> object:
    """Manually add a MeetingActionItem and optionally create a Core task."""
    task_id = None
    if create_task:
        task = graph.add_object("task", {
            "title": f"[Meeting] {text[:60]}",
            "description": text,
            "status": "candidate",
            "priority": "medium",
            "owner_ref": owner_ref,
            "due_at": due_at,
        })
        task_id = task.id

    action_item = graph.add_object("meeting_action_item", {
        "meeting_id": meeting_id,
        "text": text,
        "owner_ref": owner_ref,
        "due_at": due_at,
        "task_id": task_id,
        "status": "open",
    })
    try:
        graph.add_relation(action_item.id, meeting_id, "action_item_in")
    except Exception:
        pass
    if task_id:
        try:
            graph.add_relation(action_item.id, task_id, "action_creates_task")
        except Exception:
            pass
    return action_item


@tool(name="ingest_transcript", description="Ingest a meeting transcript to extract decisions and action items.")
def ingest_transcript(
    graph: Graph, title: str, content: str = "", date: str | None = None,
    participants: list[str] | None = None, platform: str = "other",
    duration_minutes: int | None = None,
) -> object:
    return ingest_transcript_fn(graph, title, content, date, participants, platform, duration_minutes)


@tool(name="create_meeting", description="Create a meeting record (without transcript).")
def create_meeting(
    graph: Graph, title: str, date: str | None = None,
    participants: list[str] | None = None, platform: str = "other",
    duration_minutes: int | None = None,
) -> object:
    return create_meeting_fn(graph, title, date, participants, platform, duration_minutes)


@tool(name="add_decision", description="Add a decision to an existing meeting.")
def add_decision(
    graph: Graph, meeting_id: str, text: str = "",
    decided_by: list[str] | None = None, confidence: float = 0.85,
) -> object:
    return add_decision_fn(graph, meeting_id, text, decided_by, confidence)


@tool(name="add_action_item", description="Add an action item to a meeting and create a Core task.")
def add_action_item(
    graph: Graph, meeting_id: str, text: str = "",
    owner_ref: str | None = None, due_at: str | None = None, create_task: bool = True,
) -> object:
    return add_action_item_fn(graph, meeting_id, text, owner_ref, due_at, create_task)


TOOLS = [ingest_transcript, create_meeting, add_decision, add_action_item]
