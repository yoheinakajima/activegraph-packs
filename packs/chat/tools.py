"""Chat Adapter Pack tools — v0.1."""

from __future__ import annotations

from typing import Optional

from activegraph.packs import tool


# ── Plain implementation functions (callable from fixtures/tests) ─────────────


def submit_chat_input_fn(
    graph,
    user_ref: str,
    content: str,
    session_id: Optional[str] = None,
    frame_id: Optional[str] = None,
    metadata: Optional[dict] = None,
):
    """Submit a chat message from a user. Returns the ChatInput object."""
    return graph.add_object("chat_input", {
        "user_ref": user_ref,
        "content": content,
        "session_id": session_id,
        "frame_id": frame_id,
        "role": "user",
        "metadata": metadata or {},
    })


def get_session_turns_fn(graph, session_id: str) -> list:
    """Return all ChatTurn objects for a session, ordered by turn_number.

    Graph-native: reads chat_turn objects straight from the graph rather than
    from any in-process map, so it stays correct after an API-server restart.
    Calling ``graph.objects(...)`` is safe here because tools run outside the
    behavior sandbox (unlike behaviors, which must read through ``ctx.view``).
    """
    turns = [
        t for t in graph.objects(type="chat_turn")
        if t.data.get("session_id") == session_id
    ]
    return sorted(turns, key=lambda t: t.data.get("turn_number", 0))


# ── Decorated @tool wrappers ──────────────────────────────────────────────────


@tool(
    name="submit_chat_input",
    description=(
        "Submit a chat message from a user. Creates ChatInput → triggers chat_ingester → "
        "CommMessage + ChatSession + ChatTurn → chat_llm_responder → CommResponseCandidate → "
        "chat_responder delivers response."
    ),
)
def submit_chat_input(
    graph,
    user_ref: str,
    content: str = "",
    session_id: Optional[str] = None,
    frame_id: Optional[str] = None,
):
    return submit_chat_input_fn(graph, user_ref=user_ref, content=content,
                                 session_id=session_id, frame_id=frame_id)


@tool(
    name="get_session_turns",
    description="Return all ChatTurn objects for a given session_id (ordered by turn_number).",
)
def get_session_turns(graph, session_id: str) -> list:
    return get_session_turns_fn(graph, session_id=session_id)


TOOLS = [submit_chat_input, get_session_turns]
