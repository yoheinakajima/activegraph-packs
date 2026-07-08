"""Communication Pack tools — v0.1."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from activegraph.packs import tool


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Plain implementation functions (callable from fixtures/tests) ─────────────


def create_comm_message_fn(
    graph,
    channel: str,
    content: str,
    sender_ref: str = "",
    direction: str = "inbound",
    thread_id: Optional[str] = None,
    intent_hint: Optional[str] = None,
    frame_id: Optional[str] = None,
    source_id: Optional[str] = None,
    metadata: Optional[dict] = None,
):
    """Create a channel-neutral CommMessage directly (bypassing channel adapters)."""
    return graph.add_object("comm_message", {
        "channel": channel,
        "sender_ref": sender_ref,
        "content": content,
        "direction": direction,
        "thread_id": thread_id,
        "intent_hint": intent_hint,
        "source_id": source_id,
        "created_at": _now_iso(),
        "frame_id": frame_id,
        "metadata": metadata or {},
    })


def create_response_candidate_fn(
    graph,
    message_id: str,
    channel: str,
    content: str,
    thread_id: Optional[str] = None,
    status: str = "proposed",
    artifact_id: Optional[str] = None,
    created_by_behavior: str = "manual",
    frame_id: Optional[str] = None,
):
    """Create a CommResponseCandidate for a message."""
    return graph.add_object("comm_response_candidate", {
        "message_id": message_id,
        "thread_id": thread_id,
        "channel": channel,
        "content": content,
        "artifact_id": artifact_id,
        "status": status,
        "created_by_behavior": created_by_behavior,
        "frame_id": frame_id,
    })


def approve_response_fn(graph, candidate_id: str):
    """Approve a CommResponseCandidate, triggering response_dispatcher."""
    graph.patch_object(candidate_id, {"status": "approved"})
    return graph.get_object(candidate_id)


# ── Decorated @tool wrappers ──────────────────────────────────────────────────


@tool(
    name="create_comm_message",
    description="Create a channel-neutral CommMessage directly (bypassing channel adapters).",
)
def create_comm_message(
    graph,
    channel: str,
    content: str = "",
    sender_ref: str = "",
    direction: str = "inbound",
    thread_id: Optional[str] = None,
    frame_id: Optional[str] = None,
):
    return create_comm_message_fn(
        graph, channel=channel, content=content,
        sender_ref=sender_ref, direction=direction,
        thread_id=thread_id, frame_id=frame_id,
    )


@tool(
    name="create_response_candidate",
    description="Create a CommResponseCandidate for a message.",
)
def create_response_candidate(
    graph,
    message_id: str,
    channel: str = "",
    content: str = "",
    thread_id: Optional[str] = None,
    status: str = "proposed",
):
    return create_response_candidate_fn(
        graph, message_id=message_id, channel=channel,
        content=content, thread_id=thread_id, status=status,
    )


@tool(
    name="approve_response",
    description="Approve a CommResponseCandidate, triggering response_dispatcher.",
)
def approve_response(graph, candidate_id: str):
    return approve_response_fn(graph, candidate_id=candidate_id)


TOOLS = [create_comm_message, create_response_candidate, approve_response]
