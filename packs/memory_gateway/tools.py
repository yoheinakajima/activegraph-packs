"""Memory Gateway Pack tools — v0.1.

retrieve_memories: queries the memory backend by a natural language or
keyword query and returns ranked MemoryItems.

Behaviors that need contextual memory should call retrieve_memories_fn
(the raw function), not the @tool-decorated object.
"""

from __future__ import annotations

from typing import Any, Optional

from activegraph.packs import tool


# ------------------------------------------------------------------ raw function (callable directly)


def retrieve_memories_fn(
    query: str,
    top_k: int = 10,
    min_score: float = 0.2,
    category: Optional[str] = None,
    behavior_name: Optional[str] = None,
    frame_id: Optional[str] = None,
    backend_url: str = ":memory:",
    subject_ref: Optional[str] = None,
    subject_scoped: bool = False,
    include_global: bool = True,
    exclude_frame_id: Optional[str] = None,
    ctx: Any = None,
) -> list[dict[str, Any]]:
    """Query the memory backend and return ranked results.

    Args:
        query: Natural language or keyword query
        top_k: Maximum results to return
        min_score: Minimum similarity score (0.0–1.0)
        category: Optional filter by memory category
        behavior_name: Caller behavior name (for audit)
        frame_id: Optional frame scope
        backend_url: Backend database URL (default: in-memory SQLite)
        subject_ref: The user/subject the query is on behalf of
        subject_scoped: When True, only return memories for subject_ref — an
            access-control boundary that prevents recalling another user's
            memories. Default False (global recall).
        include_global: When subject_scoped, also include subject-less (NULL)
            "global" memories. True (default) for backward compatibility; pass
            False for strict per-user isolation (no shared/legacy NULL rows).
        ctx: The caller's behavior ``ctx`` (or a ``Runtime``). When it can
            serve embeddings, the query embedding rides the runtime's
            RECORDED path (embedding.requested/responded events, replay —
            P10) instead of the process-global embedder. Omit for the
            exact legacy behavior.

    Returns:
        List of dicts sorted by outcome-adjusted relevance:
        [{item_id, text, score, raw_score, reliability_verdict,
          reliability_multiplier, category, confidence}]
    """
    from .backend import get_backend, runtime_recorded_embedding

    backend = get_backend(backend_url)
    with runtime_recorded_embedding(ctx):
        results = backend.retrieve_by_query(
            query=query,
            top_k=top_k,
            min_score=min_score,
            category=category,
            subject_ref=subject_ref,
            subject_scoped=subject_scoped,
            include_global=include_global,
            exclude_frame_id=exclude_frame_id,
        )

    # Update retrieval stats for returned items
    for r in results:
        try:
            backend.update_retrieval(r["item_id"])
        except Exception:
            pass

    return results


# ------------------------------------------------------------------ tool wrapper (for pack registration)


@tool(
    name="retrieve_memories",
    description=(
        "Retrieve relevant MemoryItems for a query. "
        "Returns outcome-adjusted relevance plus raw relevance and reliability. "
        "Use this instead of querying the graph directly for memory context."
    ),
)
def retrieve_memories(
    query: str,
    top_k: int = 10,
    min_score: float = 0.2,
    category: Optional[str] = None,
    behavior_name: Optional[str] = None,
    frame_id: Optional[str] = None,
    backend_url: str = ":memory:",
    subject_ref: Optional[str] = None,
    subject_scoped: bool = False,
    include_global: bool = True,
) -> list[dict[str, Any]]:
    """Registered tool wrapper — delegates to retrieve_memories_fn."""
    return retrieve_memories_fn(
        query=query,
        top_k=top_k,
        min_score=min_score,
        category=category,
        behavior_name=behavior_name,
        frame_id=frame_id,
        backend_url=backend_url,
        subject_ref=subject_ref,
        subject_scoped=subject_scoped,
        include_global=include_global,
    )


TOOLS = [retrieve_memories]
