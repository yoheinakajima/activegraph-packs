from __future__ import annotations

import hashlib
from typing import Any, Literal

from activegraph.packs import tool


def review_subject_fact_fn(graph, candidate_id: str, decision: Literal["confirm", "reject", "correct"], *, subject_ref: str = "owner", corrected_value: str | None = None, decided_by: str = "owner", rationale: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    verdict = graph.add_object("subject_fact_verdict", {
        "candidate_id": candidate_id, "subject_ref": subject_ref,
        "decision": decision, "corrected_value": corrected_value,
        "decided_by": decided_by, "rationale": rationale, "status": "proposed",
        "result_fact_id": None, "metadata": dict(metadata or {}),
    })
    graph.add_relation(verdict.id, candidate_id, "verdict_for_profile_candidate")
    return {"verdict_id": verdict.id, "status": "proposed"}


def forget_subject_fact_fn(graph, fact_id: str, *, forgotten_by: str = "owner", rationale: str = "owner requested forget") -> dict[str, Any]:
    fact = graph.get_object(fact_id)
    if fact is None or fact.type != "subject_fact":
        raise ValueError(f"unknown subject_fact {fact_id!r}")
    data = fact.data or {}
    raw = f"{fact_id}\x1f{forgotten_by}\x1f{rationale}".encode()
    tombstone = graph.add_object("subject_fact", {
        "fact_identity": f"subject_fact_forget_{hashlib.sha256(raw).hexdigest()[:24]}",
        "subject_ref": data["subject_ref"], "attribute": data["attribute"],
        "value": data["value"], "text": data["text"], "status": "forgotten",
        "confidence": data.get("confidence", 0.5), "trust": data.get("trust", 0.5),
        "candidate_id": data.get("candidate_id"), "annotation_id": data.get("annotation_id"),
        "evidence_id": data.get("evidence_id"), "source_surface_id": data.get("source_surface_id"),
        "verdict_id": data.get("verdict_id"), "supersedes_fact_id": fact_id,
        "metadata": {"forgotten_by": forgotten_by, "rationale": rationale},
    })
    graph.patch_object(
        fact_id, {"status": "superseded"},
        actor=forgotten_by, rationale=rationale,
    )
    graph.add_relation(tombstone.id, fact_id, "subject_fact_supersedes")
    return {"fact_id": fact_id, "tombstone_fact_id": tombstone.id, "status": "forgotten"}


@tool(name="review_subject_fact", description="Confirm, reject, or correct an evidence-backed profile candidate.")
def review_subject_fact(graph, candidate_id: str, decision: str = "confirm", subject_ref: str = "owner", corrected_value: str | None = None) -> dict[str, Any]:
    return review_subject_fact_fn(graph, candidate_id, decision, subject_ref=subject_ref, corrected_value=corrected_value)


@tool(name="forget_subject_fact", description="Forget a subject fact through an auditable superseding tombstone.")
def forget_subject_fact(graph, fact_id: str) -> dict[str, Any]:
    return forget_subject_fact_fn(graph, fact_id)


TOOLS = [review_subject_fact, forget_subject_fact]
