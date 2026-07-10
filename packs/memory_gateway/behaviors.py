"""Memory Gateway Pack behaviors — v0.1.

Four behaviors covering the full memory lifecycle:

1. candidate_evaluator — on memory_candidate.created, scores the candidate
   and accepts/rejects it. Creates an evaluation with judgment.

2. memory_writer — on evaluation.created (judgment=accepted,
   subject_type=memory_candidate), promotes candidate to MemoryItem.

3. memory_retriever — on memory_retrieval_request.created, queries the
   backend, creates MemoryRetrieval with item_ids, creates fulfilled_by
   relation. This makes all retrieval requests graph-visible and auditable.

4. memory_ranker — on memory_retrieval.created, scores each retrieved
   MemoryItem against the query and creates MemoryRanking objects.

Design rules:
- @behavior signature: no 'description' param — put it in docstring
- candidate_evaluator uses the MemoryGatewaySettings.acceptance_threshold
- memory_retriever is the graph-driven path: create memory_retrieval_request,
  let the behavior fire, read results from memory_retrieval.item_ids
- memory_ranker fires on memory_retrieval.created (whether from retriever
  or created directly)
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from activegraph.packs import behavior

from .backend import get_backend, lexical_score
from .object_types import MemoryItem, MemoryRanking, MemoryRetrieval
from .settings import MemoryGatewaySettings


# ------------------------------------------------------------------ helpers


def _derive_subject_ref(graph, source_ids) -> Optional[str]:
    """Resolve the originating subject (user) from a candidate's source objects.

    Memory candidates from the generic Core path don't carry a subject_ref, but
    they DO reference the `source` object the memory came from, and that source
    carries the originating `sender_ref`. Returns the first sender_ref found, or
    None when no source resolves to an author (a genuinely subject-less memory).
    """
    for sid in source_ids or []:
        try:
            src = graph.get_object(sid)
        except Exception:
            continue
        if not src:
            continue
        sender = (getattr(src, "data", None) or {}).get("sender_ref")
        if sender:
            return sender
    return None


# ------------------------------------------------------------------ behaviors


# Guidance categories: memories that steer the assistant's future behavior.
# These carry more authority than knowledge, so their provenance bar is higher.
_GUIDANCE_CATEGORIES = ("instruction", "preference", "decision")

# Trusted roles whose non-conversational statements may become guidance.
_TRUSTED_ROLES = ("owner", "admin", "collaborator")


def _provenance_verdict(graph, candidate_data: dict, settings) -> tuple[bool, str]:
    """Decide whether this candidate's ORIGIN permits memorization.

    The principle: conversations build memory; documents don't give orders.
    A chat_message source means the speaker is talking TO the assistant (and
    the reply gate already governs who converses; the memory is
    subject-scoped to them) — admit. Content extracted from anything else
    (emails, documents, tool results) may become knowledge, but GUIDANCE
    (instruction/preference/decision) requires the sender to resolve to a
    trusted principal — a third party's "please review the attached" must
    not become standing guidance.

    Enforced only when identity verification is possible (Identity/Auth
    loaded and principals registered); without it, behavior is unchanged —
    the same graceful-degradation rule the gateway's approver check uses.

    Returns (admit, reason). The reason lands in the evaluation rationale
    either way, so admissions are as auditable as rejections.
    """
    if settings.provenance_admission == "off":
        return True, "admission policy off"

    # Where did this text come from?
    sender_ref = None
    kind = None
    for sid in candidate_data.get("source_ids") or []:
        try:
            src = graph.get_object(sid)
        except Exception:
            src = None
        if src is None:
            continue
        sdata = src.data or {}
        kind = sdata.get("kind")
        if sdata.get("sender_ref"):
            sender_ref = sdata["sender_ref"]
            break

    if kind == "chat_message":
        return True, "conversational source (speaker addresses the assistant)"
    if not sender_ref:
        return True, "no sender provenance (internal/system content)"
    if candidate_data.get("category") not in _GUIDANCE_CATEGORIES:
        return True, f"knowledge category from {sender_ref!r} (subject-scoped)"

    # Guidance from a non-conversational source: verify the sender if we can.
    try:
        from packs.identity_auth.behaviors import (
            principals_registered,
            resolve_known_principal,
        )
    except Exception:
        return True, "identity_auth not installed — provenance unverifiable"
    if not principals_registered():
        return True, "no principals registered — provenance unverifiable"

    principal = resolve_known_principal(graph, sender_ref)
    role = principal.get("role") if principal else None
    if role in _TRUSTED_ROLES:
        return True, f"guidance from trusted principal {sender_ref!r} ({role})"
    return False, (
        f"guidance category {candidate_data.get('category')!r} from "
        f"non-conversational source by untrusted sender {sender_ref!r} "
        f"(role={role!r}) — documents don't give orders"
    )


@behavior(
    name="candidate_evaluator",
    on=["object.created"],
    where={"object.type": "memory_candidate"},
    creates=["evaluation"],
)
def candidate_evaluator(event, graph, ctx, *, settings: MemoryGatewaySettings):
    """Evaluate a memory_candidate and accept or reject it.

    On: object.created (memory_candidate)
    Creates: evaluation (judgment=accepted or rejected)
    Side effects: patches memory_candidate.accepted=True if accepted

    Acceptance criteria:
    - confidence >= acceptance_threshold
    - OR category in auto_accept_categories AND confidence >= threshold

    The evaluation object records the judgment and rationale.
    memory_writer then picks up accepted candidates.
    """
    obj = event.payload.get("object", {})
    candidate_id = obj.get("id")
    candidate_data = obj.get("data", {})

    confidence = candidate_data.get("confidence", 0.0)
    category = candidate_data.get("category")
    text = candidate_data.get("text", "")
    frame_id = candidate_data.get("frame_id")

    if not text or not candidate_id:
        return

    # ── Provenance admission: whose words become memory ────────────────────
    # The evaluator is the governance point, so admission decisions live
    # HERE, not scattered across proposers. Rejections carry a written
    # rationale — auditable, never silent.
    admit, prov_reason = _provenance_verdict(graph, candidate_data, settings)

    # ── Threshold: category priority relieves the bar, never suspends it ───
    is_priority_category = category in settings.auto_accept_categories
    threshold = (
        settings.auto_accept_min_confidence if is_priority_category
        else settings.acceptance_threshold
    )

    if not admit:
        judgment = "rejected"
        rationale = f"Provenance: {prov_reason}"
        accepted = False
    elif confidence >= threshold:
        judgment = "accepted"
        rationale = (
            f"Confidence {confidence:.2f} >= threshold {threshold:.2f}"
            + (f" (relieved for priority category '{category}')." if is_priority_category else ".")
            + f" Provenance: {prov_reason}"
        )
        accepted = True
    else:
        judgment = "rejected"
        rationale = (
            f"Confidence {confidence:.2f} < threshold {threshold:.2f}. "
            "Candidate does not meet the minimum quality bar."
        )
        accepted = False

    # Create evaluation object
    eval_obj = graph.add_object(
        "evaluation",
        {
            "subject_id": candidate_id,
            "subject_type": "memory_candidate",
            "judgment": judgment,
            "rationale": rationale,
            "evaluator": "memory_gateway.candidate_evaluator",
            "score": confidence,
            "frame_id": frame_id,
            "metadata": {
                "category": category,
                "threshold": threshold,
            },
        },
    )

    # Create evaluates relation: evaluation → memory_candidate
    try:
        graph.add_relation(eval_obj.id, candidate_id, "evaluates")
    except Exception:
        pass

    # Patch memory_candidate.accepted and evaluation_id
    if accepted:
        try:
            graph.patch_object(candidate_id, {
                "accepted": True,
                "evaluation_id": eval_obj.id,
            })
        except Exception:
            pass


@behavior(
    name="memory_writer",
    on=["object.created"],
    where={"object.type": "evaluation"},
    creates=["memory_item"],
)
def memory_writer(event, graph, ctx, *, settings: MemoryGatewaySettings):
    """Promote an accepted memory_candidate to a MemoryItem.

    On: object.created (evaluation, judgment=accepted, subject_type=memory_candidate)
    Creates: memory_item (stored in backend)
    Creates: accepted_as(memory_candidate → memory_item) relation

    Only fires for evaluations that accepted a memory_candidate.
    Writes the MemoryItem to the configured backend.
    """
    obj = event.payload.get("object", {})
    eval_data = obj.get("data", {})

    judgment = eval_data.get("judgment", "")
    subject_type = eval_data.get("subject_type", "")
    subject_id = eval_data.get("subject_id", "")

    if judgment != "accepted" or subject_type != "memory_candidate":
        return

    # Fetch the candidate from the graph
    try:
        candidate = graph.get_object(subject_id)
    except Exception:
        return

    if not candidate:
        return

    candidate_data = candidate.data
    now = datetime.now(timezone.utc).isoformat()
    text = candidate_data.get("text", "")
    subject_ref = candidate_data.get("subject_ref")

    # Access control: an untagged candidate (subject_ref missing) must NOT become
    # a global, all-users-can-recall memory just because its proposer didn't set a
    # subject — that re-opens the cross-user leak. Core's generic source→
    # observation→candidate path never sets subject_ref, so derive it from the
    # candidate's source object(s), which carry the originating sender_ref. Only a
    # candidate with no resolvable author stays subject-less (genuinely global).
    if subject_ref is None:
        subject_ref = _derive_subject_ref(graph, candidate_data.get("source_ids", []))

    backend = get_backend(settings.backend_url)

    # Dedup: a single chat message can produce the same candidate twice — once
    # from Core's generic source→observation→candidate path and once from the
    # chat heuristic write-path. Collapse them: if an item with this exact
    # (normalized) text already exists in the SAME subject scope, link the
    # candidate to it and skip the duplicate write. Dedup is subject-scoped so
    # two DIFFERENT users stating the same sentence keep separate memories.
    existing_id = backend.find_by_text(text, subject_ref=subject_ref)
    if existing_id:
        # If the surviving item is subject-less but THIS candidate knows the
        # subject (e.g. Core's subject-less candidate landed first, then the
        # chat candidate), upgrade the item's scope so recall stays isolated.
        if subject_ref is not None and backend.get_subject(existing_id) is None:
            try:
                backend.set_subject(existing_id, subject_ref)
                graph.patch_object(existing_id, {"subject_ref": subject_ref})
            except Exception:
                pass
        try:
            graph.add_relation(subject_id, existing_id, "accepted_as")
        except Exception:
            pass
        return

    item = graph.add_object(
        "memory_item",
        MemoryItem(
            text=text,
            category=candidate_data.get("category"),
            confidence=candidate_data.get("confidence", 0.7),
            source_ids=candidate_data.get("source_ids", []),
            candidate_id=subject_id,
            created_at=now,
            last_retrieved_at=None,
            retrieval_count=0,
            subject_ref=subject_ref,
        ).model_dump(),
    )

    # Store in backend
    backend.store_item(
        item_id=item.id,
        text=candidate_data.get("text", ""),
        category=candidate_data.get("category"),
        confidence=candidate_data.get("confidence", 0.7),
        # frame_id records the frame the memory was BORN in, which is what
        # lets retrieval exclude same-frame items (see retrieve_by_query's
        # exclude_frame_id): recall must never return a memory created by
        # the very turn that is asking.
        metadata={"candidate_id": subject_id, "created_at": now,
                  "frame_id": candidate_data.get("frame_id")},
        subject_ref=subject_ref,
    )

    # Enforce max_items limit
    if settings.max_items > 0:
        backend.enforce_limit(settings.max_items)

    # Create accepted_as relation: memory_candidate → memory_item
    try:
        graph.add_relation(subject_id, item.id, "accepted_as")
    except Exception:
        pass


@behavior(
    name="memory_retriever",
    on=["object.created"],
    where={"object.type": "memory_retrieval_request"},
    creates=["memory_retrieval"],
)
def memory_retriever(event, graph, ctx, *, settings: MemoryGatewaySettings):
    """Fulfill a memory_retrieval_request by querying the backend.

    On: object.created (memory_retrieval_request)
    Creates: memory_retrieval (with item_ids populated)
    Creates: fulfilled_by(memory_retrieval_request → memory_retrieval) relation

    This behavior is the graph-driven retrieval path. Creating a
    memory_retrieval_request object triggers this behavior, which queries
    the backend and creates a MemoryRetrieval with item_ids. Then
    memory_ranker fires on memory_retrieval.created and scores the results.

    To use: add a memory_retrieval_request to the graph and call
    rt.run_until_idle() — the results will be in the memory_retrieval
    and memory_ranking objects.
    """
    obj = event.payload.get("object", {})
    request_id = obj.get("id")
    request_data = obj.get("data", {})

    query = request_data.get("query", "")
    top_k = request_data.get("top_k", settings.retrieval_top_k)
    min_score = request_data.get("min_score", settings.min_retrieval_score)
    category = request_data.get("category")
    behavior_name = request_data.get("behavior_name")
    frame_id = request_data.get("frame_id")
    backend_url = request_data.get("backend_url", settings.backend_url)

    if not query or not request_id:
        return

    now = datetime.now(timezone.utc).isoformat()

    # Query the backend
    backend = get_backend(backend_url)
    results = backend.retrieve_by_query(
        query=query,
        top_k=top_k,
        min_score=min_score,
        category=category,
    )

    item_ids = [r["item_id"] for r in results]

    # Update retrieval stats in backend
    for item_id in item_ids:
        try:
            backend.update_retrieval(item_id)
        except Exception:
            pass

    # Create MemoryRetrieval object
    retrieval = graph.add_object(
        "memory_retrieval",
        MemoryRetrieval(
            query=query,
            request_id=request_id,
            behavior_name=behavior_name,
            frame_id=frame_id,
            results_count=len(item_ids),
            item_ids=item_ids,
            retrieved_at=now,
            metadata={
                "backend_results": {
                    result["item_id"]: {
                        "score": result.get("score", 0.0),
                        "raw_score": result.get("raw_score", result.get("score", 0.0)),
                        "reliability_verdict": result.get("reliability_verdict", "weak"),
                        "reliability_multiplier": result.get("reliability_multiplier", 1.0),
                    }
                    for result in results
                }
            },
        ).model_dump(),
    )

    # Create fulfilled_by relation: request → retrieval
    try:
        graph.add_relation(request_id, retrieval.id, "fulfilled_by")
    except Exception:
        pass

    # Create ranked_in relations: memory_item → memory_retrieval
    for item_id in item_ids:
        try:
            graph.add_relation(item_id, retrieval.id, "ranked_in")
        except Exception:
            pass


@behavior(
    name="memory_ranker",
    on=["object.created"],
    where={"object.type": "memory_retrieval"},
    creates=["memory_ranking"],
)
def memory_ranker(event, graph, ctx, *, settings: MemoryGatewaySettings):
    """Score each MemoryItem returned in a retrieval by query relevance.

    On: object.created (memory_retrieval)
    Creates: memory_ranking for each item in retrieval.item_ids
    Creates: scored_by(memory_ranking → memory_retrieval) relation

    Uses the shared lexical relevance score (max of Jaccard overlap and
    query-term coverage — see backend.lexical_score).
    Domain packs can add LLM-backed rerankers in their own behaviors.
    """
    obj = event.payload.get("object", {})
    retrieval_id = obj.get("id")
    retrieval_data = obj.get("data", {})

    query = retrieval_data.get("query", "")
    item_ids = retrieval_data.get("item_ids", [])

    if not query or not item_ids or not retrieval_id:
        return

    scored: list[tuple[float, str, str, dict]] = []
    backend_results = (retrieval_data.get("metadata") or {}).get("backend_results", {})

    for item_id in item_ids:
        try:
            item = graph.get_object(item_id)
        except Exception:
            continue
        if not item:
            continue

        item_text = item.data.get("text", "")
        backend_result = dict(backend_results.get(item_id) or {})
        score = float(backend_result.get("score", lexical_score(query, item_text)))
        scored.append((score, item_id, item_text, backend_result))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    for rank, (score, item_id, item_text, backend_result) in enumerate(scored, start=1):
        # Do NOT re-apply min_retrieval_score here — the retriever already
        # filtered by min_score before populating retrieval.item_ids.
        # Ranking all returned items ensures the graph audit trail is complete.

        ranking = graph.add_object(
            "memory_ranking",
            MemoryRanking(
                retrieval_id=retrieval_id,
                item_id=item_id,
                score=round(score, 4),
                reason=(
                    f"Outcome-aware relevance {score:.2f}; reliability "
                    f"{backend_result.get('reliability_verdict', 'weak')} at "
                    f"×{backend_result.get('reliability_multiplier', 1.0):.2f}."
                ),
                rank=rank,
                metadata=backend_result,
            ).model_dump(),
        )

        try:
            graph.add_relation(ranking.id, retrieval_id, "scored_by")
        except Exception:
            pass


@behavior(
    name="memory_reliability_applier",
    on=["reliability.changed"],
    creates=[],
)
def memory_reliability_applier(event, graph, ctx, *, settings: MemoryGatewaySettings):
    """Apply outcome-derived reliability to retrieval, reversibly and visibly."""

    payload = event.payload or {}
    if payload.get("artifact_type") != "memory_item":
        return
    item_id = str(payload.get("artifact_id") or "")
    item = graph.get_object(item_id)
    if item is None or item.type != "memory_item":
        return
    verdict = str(payload.get("verdict") or "weak")
    multiplier = float(payload.get("retrieval_multiplier", 1.0))
    backend = get_backend(settings.backend_url)
    backend.set_reliability(item_id, verdict, multiplier)
    graph.patch_object(
        item_id,
        {
            "reliability_verdict": verdict,
            "reliability_multiplier": multiplier,
            "reliability_event_id": event.id,
        },
    )


BEHAVIORS = [
    candidate_evaluator,
    memory_writer,
    memory_retriever,
    memory_ranker,
    memory_reliability_applier,
]
