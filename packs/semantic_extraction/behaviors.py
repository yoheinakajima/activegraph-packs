"""Eager extraction and the per-domain candidate projectors.

Three responsibilities, deliberately separated:

1. Seed the ``extraction_profile`` config artifact (D042) so facet policy
   lives in the graph, owner-editable, from the first boot.
2. Annotate every new evidence revision per the active profile
   (annotations only — never candidates; ADR 0026).
3. Project domain candidates (profile, memory) FROM annotations. This is
   domain policy over the shared layer; promotion gates downstream are
   unchanged, and each domain still dedupes its own records.
"""

from __future__ import annotations

from activegraph.packs import behavior

from .engine import run_annotation_extraction, stable_id
from .settings import SemanticExtractionSettings


_VIEW = {
    "include_types": [
        "activity_evidence",
        "semantic_annotation",
        "extraction_run",
        "extraction_coverage",
        "extraction_profile",
        "annotation_extractor_state",
    ]
}


@behavior(
    name="seed_extraction_profile",
    on=["pack.loaded"],
    where={"name": "semantic_extraction"},
    view={"include_types": ["extraction_profile"]},
    creates=["extraction_profile"],
)
def seed_extraction_profile(event, graph, ctx, *, settings: SemanticExtractionSettings):
    """Write extraction_profile v1 from the settings floor if none exists.

    Idempotent across boots: replayed stores already contain the profile
    object, so the fresh pack.loaded event each boot appends creates
    nothing new (byte-equivalent projections at a fixed horizon).
    """
    if not settings.seed_default_profile:
        return
    if any(True for _ in ctx.view.objects(type="extraction_profile")):
        return
    graph.add_object(
        "extraction_profile",
        {
            "profile_identity": stable_id("extraction_profile", 1),
            "version": 1,
            "status": "active",
            "default_facets": sorted(settings.default_profile_facets),
            "facets_by_source_category": {},
            "created_by": "semantic_extraction.seed",
            "rationale": "default eager floor (D041): entities, assertions, "
            "preferences, questions, explicit dates",
            "supersedes_profile_id": None,
        },
    )


@behavior(
    name="annotate_evidence",
    on=["object.created"],
    where={"object.type": "activity_evidence"},
    view=_VIEW,
    creates=[
        "extraction_run",
        "extraction_coverage",
        "semantic_annotation",
    ],
)
def annotate_evidence(event, graph, ctx, *, settings: SemanticExtractionSettings):
    """Run the configured extractor eagerly over each new evidence revision."""
    if not settings.enabled:
        return
    wrapper = event.payload.get("object", {})
    evidence_id = wrapper.get("id")
    evidence_obj = graph.get_object(evidence_id) if evidence_id else None
    if evidence_obj is None or evidence_obj.data.get("status") != "current":
        return
    run_annotation_extraction(
        graph,
        evidence_obj,
        settings=settings,
        reader=ctx.view,
    )


def _existing_candidate_identities(view, candidate_type: str) -> set[str]:
    return {
        obj.data.get("candidate_identity")
        for obj in view.objects(type=candidate_type)
    }


@behavior(
    name="project_profile_candidates",
    on=["object.created"],
    where={"object.type": "semantic_annotation"},
    view={"include_types": ["semantic_annotation", "profile_candidate"]},
    creates=["profile_candidate"],
)
def project_profile_candidates(event, graph, ctx, *, settings: SemanticExtractionSettings):
    """Domain policy: which annotations merit a profile candidate.

    v1 policy: concrete identity handles (handle/email/url mentions),
    subject-attributed assertions, and preference expressions. Everything
    stays a candidate — promotion is a verdict or a governed gate, never
    this projector.
    """
    if not (settings.enabled and settings.mint_profile_candidates):
        return
    wrapper = event.payload.get("object", {})
    data = wrapper.get("data", {})
    if data.get("status") != "active":
        return
    facet = data.get("facet")
    body = data.get("body") or {}

    if facet == "entity_mention" and body.get("kind") in ("handle", "email", "url"):
        attribute = body["kind"]
        value = body.get("normalized") or body.get("text", "")
    elif facet == "assertion" and data.get("attribution") in (
        "subject_self",
        "author_about_subject",
    ):
        attribute = "profile_statement"
        value = body.get("text", "")
    elif facet == "preference_expression":
        attribute = "preference"
        value = body.get("text", "")
    else:
        return
    if not value:
        return

    candidate_identity = stable_id(
        "candidate", "profile", data.get("annotation_identity")
    )
    if candidate_identity in _existing_candidate_identities(
        ctx.view, "profile_candidate"
    ):
        return
    annotation_id = wrapper.get("id")
    candidate = graph.add_object(
        "profile_candidate",
        {
            "candidate_identity": candidate_identity,
            "text": body.get("text", value),
            "confidence": data.get("confidence", 0.5),
            "evidence_id": data.get("evidence_id"),
            "evidence_identity": data.get("evidence_identity"),
            "revision_id": data.get("revision_id"),
            "extraction_record_id": data.get("run_id"),
            "extractor_id": data.get("extractor_id"),
            "extractor_version": data.get("extractor_version"),
            "extraction_config_id": data.get("config_hash"),
            "status": "candidate",
            "invalidation_reason": None,
            "metadata": {
                "projector": "semantic_extraction.profile",
                "annotation_id": annotation_id,
                "annotation_identity": data.get("annotation_identity"),
                "facet": facet,
                "polarity": data.get("polarity"),
            },
            "attribute": attribute,
            "value": value,
        },
    )
    graph.add_relation(candidate.id, annotation_id, "projected_from_annotation")
    graph.add_relation(candidate.id, data.get("evidence_id"), "extracted_from")


@behavior(
    name="project_memory_candidates",
    on=["object.created"],
    where={"object.type": "semantic_annotation"},
    view={"include_types": ["semantic_annotation", "memory_candidate"]},
    creates=["memory_candidate"],
)
def project_memory_candidates(event, graph, ctx, *, settings: SemanticExtractionSettings):
    """Domain policy: stated assertions become memory candidates.

    Memory Gateway owns acceptance downstream; observation_ids carries
    the annotation id so the provenance chain (memory candidate →
    annotation → evidence) stays walkable and dedup stays domain-local.
    """
    if not (settings.enabled and settings.mint_memory_candidates):
        return
    wrapper = event.payload.get("object", {})
    data = wrapper.get("data", {})
    if data.get("status") != "active" or data.get("facet") != "assertion":
        return
    if data.get("modality") != "stated":
        return
    annotation_id = wrapper.get("id")
    for existing in ctx.view.objects(type="memory_candidate"):
        if annotation_id in (existing.data.get("observation_ids") or []):
            return
    body = data.get("body") or {}
    text = body.get("text", "")
    if not text:
        return
    candidate = graph.add_object(
        "memory_candidate",
        {
            "text": text,
            "confidence": data.get("confidence", 0.5),
            "source_ids": [data.get("evidence_id")],
            "observation_ids": [annotation_id],
            "category": "context",
            "subject_ref": None,
            "accepted": False,
            "evaluation_id": None,
            "frame_id": None,
        },
    )
    graph.add_relation(candidate.id, annotation_id, "projected_from_annotation")


BEHAVIORS = [
    seed_extraction_profile,
    annotate_evidence,
    project_profile_candidates,
    project_memory_candidates,
]

__all__ = ["BEHAVIORS"]
