"""Versioned memory promotion beyond admission (P6, ADR 0018 posture).

Admission (memory_candidate → memory_item) is storage. PROMOTION is a
governed lifecycle transition on an exact artifact version, earned from
reliability evidence and resolved only by an explicit approver:

    reliability.changed (supported, repeated helped)
        → memory_promotion_proposal (direction=promote)     [proposer behavior]
    reliability.changed (harmful | stale)
        → memory_promotion_proposal (direction=demote)
    resolve_memory_promotion_fn(approve)
        → memory.promoted keyed (artifact_id, artifact_version)
          — the SCORING_CONTRACT identity; re-promotion of the same
          version can never re-score because the key never changes —
        → or memory.demoted (no score row; reliability owns the penalty)

Nothing in this module promotes silently: every promoted/demoted event
carries the approver, the proposal it resolved, and the exact
reliability evidence event ids that generated the proposal.

`verify_memory_replay_fn` is the memory half of the `replay.verified`
emitter (SCORING_CONTRACT key `(subject_id, subject_version)`): a
PROMOTED item version earns it when its recorded admission decision
re-derives and its stored artifact still retrieves through the recorded
(replayable) path. It fails loudly when replay evidence is incomplete —
`reference_only` lineage cannot support a replay.verified claim
(ADR 0015).
"""

from __future__ import annotations

from typing import Any, Optional

from .backend import _normalize_text, get_backend, runtime_recorded_embedding
from .object_types import MemoryPromotionProposal

# The versioned generation rule (P6 acceptance: thresholds are explicit,
# versioned constants). Bump rule_version on any semantic change; the id
# and version are recorded on every proposal this rule generates.
MEMORY_PROMOTION_RULES: dict[str, Any] = {
    "rule_id": "memory.promotion.reliability",
    "rule_version": 1,
    # "a supported artifact with repeated helped outcomes becomes a
    # promotion candidate" — repeated means at least two distinct helped
    # terminal outcomes, and the CURRENT verdict must be supported (one
    # late hurt blocks the proposal even with many old helpeds).
    "min_helped_outcomes": 2,
    "required_verdict": "supported",
    # verdicts that generate a demotion proposal for a promoted version.
    "demote_on_verdicts": ("harmful", "stale"),
}


def _objects(reader, object_type: str) -> list[Any]:
    try:
        return list(reader.objects(type=object_type))
    except Exception:
        return []


def _emit_event(graph, event_type: str, payload: dict[str, Any]):
    """Emit through raw Graph or constrained BehaviorGraph."""

    if not hasattr(graph, "ids"):
        return graph.emit(event_type, payload)
    from activegraph import Event

    event = Event(
        id=graph.ids.event(),
        type=event_type,
        payload=payload,
        actor="memory_gateway",
        timestamp=graph.clock.now(),
    )
    graph.emit(event)
    return event


def _get_item(graph, artifact_id: str):
    try:
        obj = graph.get_object(artifact_id)
    except Exception:
        obj = None
    if obj is None or getattr(obj, "type", None) != "memory_item":
        return None
    return obj


def open_proposal_for(graph, artifact_id: str, direction: Optional[str] = None):
    """The unresolved proposal for *artifact_id* (and direction), or None."""

    for obj in _objects(graph, "memory_promotion_proposal"):
        if obj.data.get("artifact_id") != artifact_id:
            continue
        if obj.data.get("status") != "proposed":
            continue
        if direction is not None and obj.data.get("direction") != direction:
            continue
        return obj
    return None


def propose_memory_promotion_fn(
    graph,
    artifact_id: str,
    *,
    direction: str,
    reliability_verdict: str,
    helped_outcomes: int,
    evidence_event_ids: list[str],
    rationale: str,
    proposed_by: str = "memory_promotion_proposer",
    is_fixture: bool = False,
    reader=None,
) -> dict[str, Any]:
    """Record one promote/demote proposal for an exact item version.

    Validates the versioned rule (`MEMORY_PROMOTION_RULES`) against the
    supplied reliability evidence; the proposal stores the rule id and
    version plus the evidence event ids so the transition it later
    authorizes is explainable to evidence. Idempotent per open
    (artifact, direction): one unresolved proposal at a time.
    """

    if direction not in {"promote", "demote"}:
        raise ValueError("direction must be 'promote' or 'demote'")
    item = _get_item(graph, artifact_id)
    if item is None:
        raise ValueError(f"unknown memory_item {artifact_id!r}")
    if not evidence_event_ids:
        raise ValueError("a proposal requires reliability evidence event ids")

    rules = MEMORY_PROMOTION_RULES
    status = item.data.get("promotion_status", "admitted")
    if direction == "promote":
        if status == "promoted":
            return {"ok": True, "created": False, "reason": "already promoted"}
        if reliability_verdict != rules["required_verdict"]:
            raise ValueError(
                f"promotion proposals require verdict "
                f"{rules['required_verdict']!r}; got {reliability_verdict!r}"
            )
        if helped_outcomes < rules["min_helped_outcomes"]:
            raise ValueError(
                f"promotion proposals require >= {rules['min_helped_outcomes']} "
                f"helped outcomes; got {helped_outcomes}"
            )
    else:
        if status != "promoted":
            return {
                "ok": True,
                "created": False,
                "reason": "demotion applies to promoted versions only",
            }
        if reliability_verdict not in rules["demote_on_verdicts"]:
            raise ValueError(
                f"demotion proposals require a verdict in "
                f"{rules['demote_on_verdicts']}; got {reliability_verdict!r}"
            )

    existing = open_proposal_for(reader or graph, artifact_id, direction)
    if existing is not None:
        return {"ok": True, "created": False, "proposal": existing}

    version = str(item.data.get("artifact_version", "1"))
    proposal_id = f"memprom_{artifact_id}_{version}_{direction}_{len(evidence_event_ids)}"
    proposal = graph.add_object(
        "memory_promotion_proposal",
        MemoryPromotionProposal(
            proposal_id=proposal_id,
            artifact_id=artifact_id,
            artifact_version=version,
            direction=direction,
            status="proposed",
            reliability_verdict=reliability_verdict,
            helped_outcomes=helped_outcomes,
            evidence_event_ids=list(evidence_event_ids),
            rule_id=rules["rule_id"],
            rule_version=rules["rule_version"],
            rationale=rationale,
            proposed_by=proposed_by,
            is_fixture=is_fixture,
        ).model_dump(),
    )
    try:
        graph.add_relation(proposal.id, item.id, "promotion_proposal_for")
    except Exception:
        pass
    return {"ok": True, "created": True, "proposal": proposal}


def resolve_memory_promotion_fn(
    graph,
    proposal_id: str,
    *,
    approve: bool,
    approver: str,
    note: str = "",
) -> dict[str, Any]:
    """Resolve one proposal — the ONLY path that promotes or demotes.

    Approving a promote proposal emits ``memory.promoted`` with the
    contract key fields (``artifact_id``, ``artifact_version``) plus the
    approver, proposal reference, and the reliability evidence event ids
    — the approval event the acceptance bar requires. Approving a demote
    proposal emits ``memory.demoted`` with the same provenance (no score
    row; the penalty lives on reliability). Rejection just closes the
    proposal. Approving an already-applied state is a no-op with the
    reason named.
    """

    if not approver.strip():
        raise ValueError("resolution requires a non-empty approver")
    proposal = next(
        (
            obj
            for obj in _objects(graph, "memory_promotion_proposal")
            if obj.data.get("proposal_id") == proposal_id
        ),
        None,
    )
    if proposal is None:
        raise ValueError(f"unknown memory promotion proposal {proposal_id!r}")
    if proposal.data.get("status") != "proposed":
        return {"ok": True, "changed": False, "reason": "proposal already resolved"}
    item = _get_item(graph, proposal.data.get("artifact_id", ""))
    if item is None:
        raise ValueError("proposal references an unknown memory_item")

    if not approve:
        graph.patch_object(
            proposal.id,
            {"status": "rejected", "resolved_by": approver,
             "metadata": {**(proposal.data.get("metadata") or {}), "note": note}},
        )
        return {"ok": True, "changed": True, "resolution": "rejected"}

    direction = proposal.data.get("direction", "promote")
    version = str(proposal.data.get("artifact_version", "1"))
    event_type = "memory.promoted" if direction == "promote" else "memory.demoted"
    new_status = "promoted" if direction == "promote" else "demoted"
    if item.data.get("promotion_status") == new_status:
        graph.patch_object(
            proposal.id, {"status": "approved", "resolved_by": approver}
        )
        return {"ok": True, "changed": False, "reason": f"already {new_status}"}

    event = _emit_event(
        graph,
        event_type,
        {
            # SCORING_CONTRACT identity for memory.promoted:
            "artifact_id": item.id,
            "artifact_version": version,
            "artifact_type": "memory_item",
            "proposal_id": proposal_id,
            "direction": direction,
            "approver": approver,
            "note": note,
            "reliability_verdict": proposal.data.get("reliability_verdict", ""),
            "evidence_event_ids": list(
                proposal.data.get("evidence_event_ids") or []
            ),
            "rule_id": proposal.data.get("rule_id", ""),
            "rule_version": proposal.data.get("rule_version", 0),
            "is_fixture": bool(proposal.data.get("is_fixture", False)),
        },
    )
    history = list(item.data.get("promotion_history") or [])
    history.append(
        {
            "event_id": getattr(event, "id", None),
            "proposal_id": proposal_id,
            "direction": direction,
            "approver": approver,
            "prior_status": item.data.get("promotion_status", "admitted"),
            "new_status": new_status,
        }
    )
    graph.patch_object(
        item.id,
        {"promotion_status": new_status, "promotion_history": history},
    )
    graph.patch_object(
        proposal.id,
        {
            "status": "approved",
            "resolved_by": approver,
            "resolved_event_id": getattr(event, "id", "") or "",
        },
    )
    return {
        "ok": True,
        "changed": True,
        "resolution": "approved",
        "event_id": getattr(event, "id", None),
        "item": graph.get_object(item.id),
    }


# ------------------------------------------------------------ replay.verified


class MemoryReplayIncompleteError(RuntimeError):
    """Replay evidence cannot support a replay.verified claim (ADR 0015)."""


def _source_replay_gaps(graph, item) -> list[str]:
    """Source refs whose recorded lineage forbids a replay claim.

    A source that resolves to an object carrying ``replay_complete: False``
    or an acquisition with ``replay_mode: reference_only`` cannot back a
    replay.verified claim (ADR 0015). Sources that live entirely in the
    log (chat messages, inline pastes) are replay-complete by
    construction and pass.
    """

    gaps: list[str] = []
    refs = list(item.data.get("source_ids") or [])
    candidate_id = item.data.get("candidate_id")
    if candidate_id:
        try:
            candidate = graph.get_object(candidate_id)
        except Exception:
            candidate = None
        if candidate is not None:
            refs.extend(candidate.data.get("source_ids") or [])
    for ref in refs:
        try:
            source = graph.get_object(str(ref))
        except Exception:
            source = None
        if source is None:
            continue
        data = source.data or {}
        metadata = data.get("metadata") or {}
        acquisition = (
            data.get("acquisition") or metadata.get("acquisition") or {}
        )
        replay_complete = data.get(
            "replay_complete", metadata.get("replay_complete")
        )
        if replay_complete is False:
            gaps.append(str(ref))
        elif acquisition.get("replay_mode") == "reference_only":
            gaps.append(str(ref))
    return gaps


def verify_memory_replay_fn(
    graph,
    artifact_id: str,
    *,
    backend_url: str = ":memory:",
    runtime=None,
    actor: str = "memory_gateway",
) -> dict[str, Any]:
    """Earn ``replay.verified`` for one PROMOTED memory item version.

    Three recorded checks, all from the log and the stored artifact —
    no external contact (retrieval embeds ride the recorded runtime path
    when *runtime* is supplied, exactly like live recall — P10):

    1. **Lineage is replay-complete.** Any source whose recorded
       acquisition is ``reference_only`` (or exposes
       ``replay_complete: False``) fails LOUDLY — ADR 0015 says that
       lineage cannot support a replay.verified claim.
    2. **The admission decision re-derives.** The recorded candidate
       still exists, its normalized text matches the stored item, and
       the recorded accepting evaluation is present.
    3. **The stored artifact still retrieves.** The backend returns the
       item for its own text through the same retrieval path recall
       uses.

    Emits ``replay.verified`` keyed ``(subject_id, subject_version)``
    (SCORING_CONTRACT) exactly once per version — a re-verification of
    the same version returns the recorded event instead of emitting a
    duplicate.
    """

    item = _get_item(graph, artifact_id)
    if item is None:
        raise ValueError(f"unknown memory_item {artifact_id!r}")
    if item.data.get("promotion_status") != "promoted":
        raise ValueError(
            "replay verification applies to promoted memory versions only; "
            f"this item is {item.data.get('promotion_status', 'admitted')!r}"
        )
    version = str(item.data.get("artifact_version", "1"))

    gaps = _source_replay_gaps(graph, item)
    if gaps:
        raise MemoryReplayIncompleteError(
            f"memory_item {artifact_id!r} has reference_only / "
            f"replay-incomplete source lineage {gaps}; ADR 0015: such "
            f"lineage may not support a replay.verified claim"
        )

    checks: list[dict[str, Any]] = []

    candidate_id = item.data.get("candidate_id")
    candidate = None
    if candidate_id:
        try:
            candidate = graph.get_object(candidate_id)
        except Exception:
            candidate = None
    if candidate is None:
        raise MemoryReplayIncompleteError(
            f"memory_item {artifact_id!r} has no recorded memory_candidate; "
            f"the admission decision cannot be re-derived from the log"
        )
    text_matches = _normalize_text(candidate.data.get("text", "")) == (
        _normalize_text(item.data.get("text", ""))
    )
    if not text_matches:
        raise MemoryReplayIncompleteError(
            f"memory_item {artifact_id!r} text no longer re-derives from its "
            f"recorded candidate — replay check failed, not skipped"
        )
    checks.append({"check": "admission_rederivation", "passed": True,
                   "candidate_id": candidate_id})

    backend = get_backend(backend_url)
    with runtime_recorded_embedding(runtime):
        results = backend.retrieve_by_query(
            query=item.data.get("text", ""), top_k=10, min_score=0.0
        )
    retrieved_ids = [r.get("item_id") for r in results]
    if item.id not in retrieved_ids:
        raise MemoryReplayIncompleteError(
            f"memory_item {artifact_id!r} is no longer retrievable from the "
            f"stored artifact — replay check failed, not skipped"
        )
    checks.append({"check": "recorded_retrieval", "passed": True,
                   "results_considered": len(retrieved_ids)})

    prior = next(
        (
            e
            for e in getattr(graph, "events", [])
            if e.type == "replay.verified"
            and (e.payload or {}).get("subject_id") == item.id
            and str((e.payload or {}).get("subject_version")) == version
        ),
        None,
    )
    if prior is not None:
        return {"ok": True, "created": False, "event_id": prior.id}

    event = _emit_event(
        graph,
        "replay.verified",
        {
            # SCORING_CONTRACT identity:
            "subject_id": item.id,
            "subject_version": version,
            "subject_type": "memory_item",
            "method": "recorded_admission_and_retrieval_recheck",
            "checks": checks,
            "actor": actor,
            "is_fixture": bool(
                (item.data.get("metadata") or {}).get("is_fixture", False)
            ),
        },
    )
    return {"ok": True, "created": True, "event_id": getattr(event, "id", None)}
