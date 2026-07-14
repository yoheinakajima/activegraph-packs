from __future__ import annotations

import hashlib

from activegraph import Event
from activegraph.packs import behavior

from .settings import SubjectProfileSettings


def _stable_id(prefix: str, *parts) -> str:
    raw = "\x1f".join(str(part) for part in parts).encode()
    return f"{prefix}_{hashlib.sha256(raw).hexdigest()[:24]}"


def _attention_ref(prefix: str, *parts) -> str:
    """Opaque attention subject ref, matching the conversation adapter's
    derivation so a person confirmed by fact and seen in mail share one
    importance vector."""
    raw = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(raw).hexdigest()}"


def _emit_event(graph, event_type: str, payload: dict, actor: str):
    if not hasattr(graph, "ids"):
        return graph.emit(event_type, payload)
    event = Event(
        id=graph.ids.event(), type=event_type, payload=payload, actor=actor,
        timestamp=graph.clock.now(),
    )
    graph.emit(event)
    return event


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

    # Re-confirming a value the subject already holds resolves to the
    # existing fact: promotion is idempotent by value, so richer evidence
    # never mints near-duplicates for consumers to dedupe.
    existing = next(
        (
            fact for fact in ctx.view.objects(type="subject_fact")
            if fact.data.get("subject_ref") == subject_ref
            and fact.data.get("attribute") == attribute
            and fact.data.get("status") == "promoted"
            and fact.data.get("value") == value
        ),
        None,
    )
    if existing is not None:
        graph.patch_object(verdict_id, {"status": "applied", "result_fact_id": existing.id})
        return

    # An owner re-declaration supersedes the owner's own prior declaration for
    # the same attribute/platform scope (ADR 0020 applied to self-declared
    # identity): editing "my github handle" replaces the old handle instead of
    # accumulating beside it. Scope travels in verdict metadata as
    # ``declaration_scope`` (e.g. "self:handle:github"); facts without a scope
    # — independently sourced facts above all — are never superseded by it.
    declaration_scope = str((data.get("metadata") or {}).get("declaration_scope") or "")
    superseded_priors = [
        fact for fact in ctx.view.objects(type="subject_fact")
        if fact.data.get("subject_ref") == subject_ref
        and fact.data.get("status") == "promoted"
        and fact.data.get("value") != value
        and str((fact.data.get("metadata") or {}).get("declaration_scope") or "")
        == declaration_scope
    ] if declaration_scope else []
    superseded_ids = {fact.id for fact in superseded_priors}

    # Only single-valued attributes contradict; everything else accumulates.
    # A prior this declaration supersedes is a correction, not a conflict.
    active = [
        fact for fact in ctx.view.objects(type="subject_fact")
        if fact.data.get("subject_ref") == subject_ref
        and fact.data.get("attribute") == attribute
        and fact.data.get("status") == "promoted"
        and fact.data.get("value") != value
        and fact.id not in superseded_ids
    ] if attribute in set(settings.single_valued_attributes) else []
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
        "metadata": {
            **(data.get("metadata") or {}),
            "decision": decision,
            "decided_by": data.get("decided_by"),
        },
    })
    graph.add_relation(fact.id, candidate.id, "promoted_from_profile_candidate")
    graph.add_relation(fact.id, evidence_id, "subject_fact_grounded_in")
    graph.patch_object(verdict_id, {"status": "applied", "result_fact_id": fact.id})
    for prior in superseded_priors:
        graph.patch_object(prior.id, {"status": "superseded"})
        graph.add_relation(fact.id, prior.id, "subject_fact_supersedes")
    if superseded_priors:
        graph.patch_object(
            fact.id, {"supersedes_fact_id": superseded_priors[-1].id}
        )
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


_SEED_SUBJECT_KINDS = {
    "relationship": "person",
    "person": "person",
    "company": "org",
    "organization": "org",
    "affiliation": "org",
    "project": "project",
}


@behavior(
    name="seed_importance_from_confirmed_fact",
    on=["object.created"],
    where={"object.type": "subject_fact", "object.data.status": "promoted"},
    view={"include_types": []},
    creates=[],
)
def seed_importance_from_confirmed_fact(
    event, graph, ctx, *, settings: SubjectProfileSettings
):
    """A confirmed relationship/company fact is an explicit owner act and may
    seed an importance observation (ADR 0038 rule 3, ADR 0039).

    Identity aliases (email/handle/url) never seed importance — they anchor
    interpretation instead. Trust stays strictly outcome-only: this emits an
    ``attention.signal_observed`` event, never an ``outcome.*`` event, so
    knowing who someone is can never raise how much their content is
    believed.
    """
    del ctx
    wrapper = (event.payload or {}).get("object") or {}
    data = wrapper.get("data") or {}
    attribute = str(data.get("attribute") or "")
    if attribute not in set(settings.importance_seed_attributes):
        return
    value = str(data.get("value") or "").strip()
    if not value:
        return
    normalized = value.lower()
    if "@" in normalized and "." in normalized.rsplit("@", 1)[-1]:
        # Matches the conversation adapter's opaque counterpart identity so
        # the confirmed person and the mailbox person share one vector.
        subject_kind = "person"
        subject_ref = _attention_ref("person_email", normalized)
    else:
        subject_kind = _SEED_SUBJECT_KINDS.get(attribute, "entity")
        subject_ref = _attention_ref("subject_fact_value", subject_kind, normalized)
    fact_identity = str(data.get("fact_identity") or wrapper.get("id") or "")
    evidence_refs = [
        ref for ref in (data.get("evidence_id"), wrapper.get("id")) if ref
    ]
    _emit_event(
        graph,
        "attention.signal_observed",
        {
            "producer": "subject_profile",
            "observations": [{
                "observation_id": _stable_id(
                    "attention_observation", "confirmed_fact", fact_identity
                ),
                "subject_ref": subject_ref,
                "subject_kind": subject_kind,
                "signal_type": "explicit_important",
                "strength_milli": settings.importance_seed_strength_milli,
                "context_key": "global",
                "source": "user",
                "explicit": True,
                "evidence_refs": evidence_refs,
                "metadata": {
                    "derivation": "confirmed_subject_fact",
                    "fact_identity": fact_identity,
                    "attribute": attribute,
                },
            }],
        },
        "subject_profile",
    )


BEHAVIORS = [apply_subject_fact_verdict, seed_importance_from_confirmed_fact]

