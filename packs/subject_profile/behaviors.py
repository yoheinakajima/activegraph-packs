from __future__ import annotations

import hashlib

from activegraph.packs import behavior

from .settings import SubjectProfileSettings


def _stable_id(prefix: str, *parts) -> str:
    raw = "\x1f".join(str(part) for part in parts).encode()
    return f"{prefix}_{hashlib.sha256(raw).hexdigest()[:24]}"


@behavior(
    name="apply_subject_fact_verdict",
    on=["object.created"],
    where={"object.type": "subject_fact_verdict", "object.data.status": "proposed"},
    view={"include_types": ["profile_candidate", "activity_evidence", "subject_fact"]},
    creates=["subject_fact", "subject_contradiction"],
)
def apply_subject_fact_verdict(event, graph, ctx, *, settings: SubjectProfileSettings):
    wrapper = event.payload.get("object") or {}
    verdict_id = wrapper.get("id")
    data = wrapper.get("data") or {}
    candidate = graph.get_object(str(data.get("candidate_id") or ""))
    if candidate is None or candidate.type != "profile_candidate":
        graph.patch_object(verdict_id, {"status": "failed"})
        return
    decision = data.get("decision")
    if decision == "reject":
        graph.patch_object(verdict_id, {"status": "applied"})
        graph.patch_object(candidate.id, {"status": "invalidated", "invalidation_reason": "owner_rejected"})
        return

    cdata = candidate.data or {}
    subject_ref = str(data.get("subject_ref") or settings.owner_subject_ref)
    evidence_id = str(cdata.get("evidence_id") or "")
    evidence = graph.get_object(evidence_id) if evidence_id else None
    # Scope is a security boundary. A review decision can correct a value but
    # cannot silently reinterpret multi-subject connector content as being
    # about the owner.
    if evidence is None or (evidence.data.get("normalized_metadata") or {}).get("subject_scope") != "owner_profile":
        graph.patch_object(verdict_id, {"status": "failed", "metadata": {"error": "owner_subject_scope_required"}})
        return

    attribute = str(cdata.get("attribute") or "profile_statement")
    value = str(data.get("corrected_value") or cdata.get("value") or cdata.get("text") or "").strip()
    if not value:
        graph.patch_object(verdict_id, {"status": "failed", "metadata": {"error": "empty_value"}})
        return

    active = [
        fact for fact in ctx.view.objects(type="subject_fact")
        if fact.data.get("subject_ref") == subject_ref
        and fact.data.get("attribute") == attribute
        and fact.data.get("status") == "promoted"
        and fact.data.get("value") != value
    ]
    fact = graph.add_object("subject_fact", {
        "fact_identity": _stable_id("subject_fact", subject_ref, attribute, value, candidate.id),
        "subject_ref": subject_ref, "attribute": attribute, "value": value,
        "text": str(cdata.get("text") or value), "status": "promoted",
        "confidence": float(cdata.get("confidence") or 0.5),
        "trust": settings.confirmed_trust, "candidate_id": candidate.id,
        "annotation_id": (cdata.get("metadata") or {}).get("annotation_id"),
        "evidence_id": evidence_id,
        "source_surface_id": evidence.data.get("source_surface_id"),
        "verdict_id": verdict_id, "supersedes_fact_id": None,
        "metadata": {"decision": decision, "decided_by": data.get("decided_by")},
    })
    graph.add_relation(fact.id, candidate.id, "promoted_from_profile_candidate")
    graph.add_relation(fact.id, evidence_id, "subject_fact_grounded_in")
    graph.patch_object(verdict_id, {"status": "applied", "result_fact_id": fact.id})
    for prior in active:
        graph.patch_object(prior.id, {"status": "contradicted"})
        contradiction = graph.add_object("subject_contradiction", {
            "contradiction_identity": _stable_id("subject_contradiction", subject_ref, attribute, prior.id, fact.id),
            "subject_ref": subject_ref, "attribute": attribute,
            "fact_ids": [prior.id, fact.id], "status": "open",
            "winning_fact_id": None, "rationale": "conflicting owner-confirmed values",
            "metadata": {},
        })
        graph.add_relation(contradiction.id, prior.id, "contradiction_involves")
        graph.add_relation(contradiction.id, fact.id, "contradiction_involves")


BEHAVIORS = [apply_subject_fact_verdict]

