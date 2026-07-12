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

from .engine import run_profile_extraction, stable_id
from .facets import LLM_UPGRADE_FACETS
from .settings import SemanticExtractionSettings

LLM_EXTRACTOR_REF = "semantic.llm@0.1.0"


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


def _llm_upgrade_active(settings: SemanticExtractionSettings) -> bool:
    """Whether the default profile should route the LLM-only facets to
    semantic.llm: an LLM provider is configured AND the upgrade isn't
    switched off. No provider → False → the seeded profile (and every
    downstream byte) is identical to the zero-key mode."""
    if not settings.llm_upgrade_enabled:
        return False
    from packs.llm_provider import configured_llm_provider

    return configured_llm_provider().configured


@behavior(
    name="seed_extraction_profile",
    on=["pack.loaded"],
    where={"name": "semantic_extraction"},
    view={"include_types": ["extraction_profile", "annotation_extractor_state"]},
    creates=["extraction_profile", "annotation_extractor_state"],
)
def seed_extraction_profile(event, graph, ctx, *, settings: SemanticExtractionSettings):
    """Write extraction_profile v1 from the settings floor if none exists.

    Idempotent across boots: replayed stores already contain the profile
    object, so the fresh pack.loaded event each boot appends creates
    nothing new (byte-equivalent projections at a fixed horizon).

    With an LLM provider configured (D025 stage two), the seeded default
    additionally requests the two facets the deterministic floor cannot
    produce and routes them to ``semantic.llm`` — the cheap eager floor
    itself stands unchanged (D041). The LLM extractor version is also
    recorded as a *candidate* configuration (ADR 0014): serving floor
    facets requires trial evidence plus an explicit promotion.
    """
    if not settings.seed_default_profile:
        return
    if any(True for _ in ctx.view.objects(type="extraction_profile")):
        return
    upgraded = _llm_upgrade_active(settings)
    default_facets = sorted(settings.default_profile_facets)
    extractor_by_facet: dict[str, str] = {}
    rationale = (
        "default eager floor (D041): entities, assertions, "
        "preferences, questions, explicit dates"
    )
    if upgraded:
        default_facets = sorted({*default_facets, *LLM_UPGRADE_FACETS})
        extractor_by_facet = {
            facet: LLM_EXTRACTOR_REF for facet in LLM_UPGRADE_FACETS
        }
        rationale += (
            "; relation_mention/event_mention upgraded to semantic.llm "
            "(provider configured, D025 stage two)"
        )
    graph.add_object(
        "extraction_profile",
        {
            "profile_identity": stable_id("extraction_profile", 1),
            "version": 1,
            "status": "active",
            "default_facets": default_facets,
            "facets_by_source_category": {
                category: sorted(facets)
                for category, facets in
                settings.default_facets_by_source_category.items()
            },
            "extractor_by_facet": extractor_by_facet,
            "created_by": "semantic_extraction.seed",
            "rationale": rationale,
            "supersedes_profile_id": None,
        },
    )
    if upgraded and not any(
        obj.data.get("extractor_id") == "semantic.llm"
        for obj in ctx.view.objects(type="annotation_extractor_state")
    ):
        graph.add_object(
            "annotation_extractor_state",
            {
                "state_identity": stable_id(
                    "annotation_extractor_state",
                    "semantic.llm",
                    "0.1.0",
                    "candidate",
                ),
                "extractor_id": "semantic.llm",
                "extractor_version": "0.1.0",
                "status": "candidate",
                "reason": (
                    "fork-trial-promote (ADR 0014): candidate for floor "
                    "facets pending recorded trial evidence and explicit "
                    "promotion; serves the LLM-only facets meanwhile"
                ),
                "metadata": {},
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
    """Run the profile's extractor policy eagerly over each new evidence
    revision — one cache-identified pass per extractor group."""
    if not settings.enabled:
        return
    wrapper = event.payload.get("object", {})
    evidence_id = wrapper.get("id")
    evidence_obj = graph.get_object(evidence_id) if evidence_id else None
    if evidence_obj is None or evidence_obj.data.get("status") != "current":
        return
    run_profile_extraction(
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
    view={
        "include_types": [
            "semantic_annotation", "activity_evidence", "profile_candidate"
        ]
    },
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
    evidence = next(
        (
            obj for obj in ctx.view.objects(type="activity_evidence")
            if obj.id == data.get("evidence_id")
        ),
        None,
    )
    # Profile facts require explicit owner-subject evidence. Text from a
    # multi-party source is not identity merely because it contains an
    # address, URL, preference cue, or assertion.
    if (
        evidence is None
        or (evidence.data.get("normalized_metadata") or {}).get("subject_scope")
        != "owner_profile"
    ):
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
