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


def _subject_allows_evidence(data: dict[str, Any], subject_ref: Optional[str]) -> bool:
    if subject_ref is None:
        return True
    metadata = data.get("normalized_metadata") or {}
    explicit = metadata.get("subject_ref")
    if explicit is not None:
        return str(explicit) == subject_ref
    return subject_ref == "owner" and metadata.get("subject_scope") == "owner_profile"


def resolve_memory_query_fn(
    graph,
    query: str,
    *,
    subject_ref: Optional[str] = None,
    top_k: int = 8,
    min_score: float = 0.2,
    backend_url: str = ":memory:",
    source_domain: str = "general",
    query_scope: str = "general",
) -> dict[str, Any]:
    """Resolve over registered procedures with raw evidence as the floor.

    Tier 0 and Tier 1 are available whenever their object types are loaded.
    Tier 2 is the admitted-memory backend compatibility procedure. Tier 3 is
    deliberately registered as unavailable until a governed retrieval skill
    is supplied. Higher tiers may augment, but never replace, matching source
    evidence in the returned context.
    """
    from .backend import lexical_score

    from packs.attention.tools import get_source_trust_fn

    evidence_rows = []
    try:
        evidence_objects = graph.objects(type="activity_evidence")
    except Exception:
        evidence_objects = []
    for obj in evidence_objects:
        data = obj.data or {}
        if data.get("status") != "current" or not _subject_allows_evidence(data, subject_ref):
            continue
        raw_score = lexical_score(query, str(data.get("normalized_content") or ""))
        if raw_score >= min_score:
            trust = get_source_trust_fn(
                graph,
                str(data.get("source_surface_id") or data.get("source_ref") or "unknown"),
                domain=source_domain,
                query_scope=query_scope,
            )
            # Unknown trust is neutral (1.0 multiplier), not low trust. Learned
            # credibility can reorder competing evidence but never remove the
            # evidence floor or let a derivative override its source.
            multiplier = 0.5 + (int(trust["score_milli"]) / 1_000)
            evidence_rows.append((raw_score * multiplier, raw_score, obj, trust))
    evidence_rows.sort(key=lambda row: (-row[0], -row[1], row[2].id))
    evidence_rows = evidence_rows[:top_k]
    evidence_ids = [obj.id for _weighted, _raw, obj, _trust in evidence_rows]

    annotation_rows = []
    if evidence_ids:
        try:
            annotations = graph.objects(type="semantic_annotation")
        except Exception:
            annotations = []
        for obj in annotations:
            data = obj.data or {}
            if data.get("status") != "active" or data.get("evidence_id") not in evidence_ids:
                continue
            body = data.get("body") or {}
            text = str(body.get("text") or body.get("tag") or "")
            score = lexical_score(query, text)
            if text and score >= min_score:
                annotation_rows.append((score, obj, text))
        annotation_rows.sort(key=lambda row: (-row[0], row[1].id))
        annotation_rows = annotation_rows[:top_k]

    memory_rows = retrieve_memories_fn(
        query, top_k=top_k, min_score=min_score, backend_url=backend_url,
        subject_ref=subject_ref, subject_scoped=subject_ref is not None,
        include_global=subject_ref is None,
    )

    blocks = []
    if evidence_rows:
        blocks.append("[authoritative-evidence]")
        blocks.extend(
            f"- evidence_id={obj.id} | source={obj.data.get('source_ref')} | "
            f"trust={trust.get('verdict')}:{trust.get('score_milli')} | "
            f"{str(obj.data.get('normalized_content') or '')}"
            for _weighted, _raw, obj, trust in evidence_rows
        )
    if annotation_rows:
        blocks.append("[evidence-annotations]")
        blocks.extend(
            f"- annotation_id={obj.id} | evidence_id={obj.data.get('evidence_id')} | {text}"
            for _score, obj, text in annotation_rows
        )
    if memory_rows:
        blocks.append("[admitted-memory-augmentation]")
        blocks.extend(
            f"- memory_item_id={row['item_id']} | {row.get('text', '')}"
            for row in memory_rows
        )

    if annotation_rows:
        selected_tier = "annotated_evidence"
        procedure_id = "memory.annotated_evidence@0.1.0"
    elif evidence_rows:
        selected_tier = "evidence"
        procedure_id = "memory.raw_evidence@0.1.0"
    elif memory_rows:
        selected_tier = "compiled_belief"
        procedure_id = "memory.admitted_items@0.1.0"
    else:
        selected_tier = "none"
        procedure_id = "memory.no_match@0.1.0"
    confidence = max(
        [min(score, 1.0) for score, _raw, _obj, _trust in evidence_rows]
        + [score for score, _obj, _text in annotation_rows]
        + [float(row.get("score") or 0.0) for row in memory_rows]
        + [0.0]
    )
    resolution = graph.add_object("memory_query_resolution", {
        "query": query, "subject_ref": subject_ref, "selected_tier": selected_tier,
        "procedure_id": procedure_id, "evidence_ids": evidence_ids,
        "annotation_ids": [obj.id for _score, obj, _text in annotation_rows],
        "memory_item_ids": [row["item_id"] for row in memory_rows],
        "context_text": "\n".join(blocks), "confidence": min(confidence, 1.0),
        "authoritative_evidence": True,
        "coverage": {"procedures_checked": ["raw_evidence", "annotated_evidence", "admitted_items", "live_lookup"], "live_lookup_available": False},
        "metadata": {
            "rule": "evidence_precedes_derived_artifacts",
            "trust_arbitration": [
                {
                    "evidence_id": obj.id,
                    "source_ref": trust.get("source_ref"),
                    "domain": trust.get("domain"),
                    "query_scope": trust.get("query_scope"),
                    "trust_score_milli": trust.get("score_milli"),
                    "trust_verdict": trust.get("verdict"),
                    "trust_vector_id": trust.get("object_id"),
                    "trust_evidence_refs": list(trust.get("evidence_refs") or []),
                    "raw_relevance": raw,
                    "weighted_relevance": weighted,
                }
                for weighted, raw, obj, trust in evidence_rows
            ],
        },
    })
    return {"resolution_id": resolution.id, **resolution.data}


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


@tool(
    name="resolve_memory_query",
    description=(
        "Resolve a memory query through raw evidence, annotations, admitted "
        "items, and the reserved live-lookup slot; evidence is authoritative."
    ),
)
def resolve_memory_query(
    graph,
    query: str,
    subject_ref: Optional[str] = None,
    top_k: int = 8,
    min_score: float = 0.2,
    backend_url: str = ":memory:",
    source_domain: str = "general",
    query_scope: str = "general",
) -> dict[str, Any]:
    return resolve_memory_query_fn(
        graph, query, subject_ref=subject_ref, top_k=top_k,
        min_score=min_score, backend_url=backend_url,
        source_domain=source_domain, query_scope=query_scope,
    )


TOOLS = [retrieve_memories, resolve_memory_query]
