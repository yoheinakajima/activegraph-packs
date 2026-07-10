"""The one extraction implementation shared by the behavior and the tools.

Behaviors receive the constrained BehaviorGraph and pass ``read_view``;
tools receive the full Graph and pass it for both. Either way the write
surface is ``graph`` and the cache lookups go through ``reader`` — the
logic is identical, so cache identity semantics cannot drift between
the eager path and explicit re-extraction.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from .extractor import (
    AnnotationDraft,
    AnnotationExtractor,
    DeterministicExtractorV1,
    config_hash_for,
    get_annotation_extractor,
)
from .facets import validate_body
from .settings import SemanticExtractionSettings


def stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()}"


def patch(graph, target: str, updates: dict[str, Any], *, rationale: str) -> None:
    """Patch through either graph surface (BehaviorGraph takes no rationale)."""
    if hasattr(graph, "objects"):
        graph.patch_object(target, updates, rationale=rationale)
    else:
        graph.patch_object(target, updates)


def resolve_extractor(settings: SemanticExtractionSettings) -> AnnotationExtractor:
    """Build the configured extractor.

    The deterministic default is constructed from settings so its config
    hash reflects the live bounds; any other id/version comes from the
    registry (the LLM-upgrade seam — same contract, different id).
    """
    default = DeterministicExtractorV1()
    if (
        settings.extractor_id == default.extractor_id
        and settings.extractor_version == default.extractor_version
    ):
        return DeterministicExtractorV1(
            max_content_chars=settings.max_content_chars,
            max_annotations_per_facet=settings.max_annotations_per_facet,
            min_assertion_chars=settings.min_assertion_chars,
            topic_tag_count=settings.topic_tag_count,
        )
    return get_annotation_extractor(settings.extractor_id, settings.extractor_version)


def active_profile_facets(
    reader, source_category: str, fallback: tuple[str, ...]
) -> tuple[str, ...]:
    """Requested facets for one source category, from the config artifact.

    The latest ``active`` extraction_profile decides; the settings floor
    is only the fallback for graphs where no profile exists yet.
    """
    profiles = [
        obj
        for obj in reader.objects(type="extraction_profile")
        if obj.data.get("status") == "active"
    ]
    if not profiles:
        return tuple(sorted(fallback))
    profiles.sort(key=lambda obj: obj.data.get("version", 0))
    data = profiles[-1].data
    per_category = data.get("facets_by_source_category") or {}
    facets = per_category.get(source_category) or data.get("default_facets") or []
    return tuple(sorted(facets)) if facets else tuple(sorted(fallback))


def extractor_disabled(reader, extractor_id: str, extractor_version: str) -> bool:
    states = [
        obj.data
        for obj in reader.objects(type="annotation_extractor_state")
        if obj.data.get("extractor_id") == extractor_id
        and obj.data.get("extractor_version") == extractor_version
    ]
    return bool(states and states[-1].get("status") == "disabled")


def _attribution(normalized_metadata: dict[str, Any]) -> tuple[str, Optional[str]]:
    role = normalized_metadata.get("role")
    if role in ("user", "human", "owner"):
        return "subject_self", str(role)
    if role in ("assistant", "model", "tool", "system"):
        return "author_about_subject", str(role)
    return "unknown", str(role) if role is not None else None


def _facets_covered(
    reader,
    revision_id: str,
    extractor_id: str,
    extractor_version: str,
    config_hash: str,
) -> set[str]:
    """Facets any prior completed run of this extractor identity executed."""
    covered: set[str] = set()
    for obj in reader.objects(type="extraction_run"):
        data = obj.data
        if (
            data.get("revision_id") == revision_id
            and data.get("extractor_id") == extractor_id
            and data.get("extractor_version") == extractor_version
            and data.get("config_hash") == config_hash
            and data.get("status") == "completed"
        ):
            covered.update(data.get("executed_facets") or [])
    return covered


def annotation_identity_for(
    revision_id: str,
    extractor_id: str,
    extractor_version: str,
    config_hash: str,
    draft: AnnotationDraft,
) -> str:
    body_canonical = "\x1e".join(
        f"{key}={draft.body[key]}" for key in sorted(draft.body)
    )
    return stable_id(
        "annotation",
        revision_id,
        extractor_id,
        extractor_version,
        config_hash,
        draft.facet,
        draft.start,
        draft.end,
        body_canonical,
    )


def run_annotation_extraction(
    graph,
    evidence_obj,
    *,
    settings: SemanticExtractionSettings,
    requested_facets: Optional[tuple[str, ...]] = None,
    reader=None,
) -> dict[str, Any]:
    """Execute (or cache-hit) one extraction over one evidence revision.

    Cache identity: (evidence_revision, extractor_id, extractor_version,
    config_hash, requested_facets). Same identity → the existing run is
    returned untouched. New identity → only facets not covered by prior
    runs of the same extractor identity execute; everything else lands in
    ``cached_facets`` and coverage says so.
    """
    reader = reader or graph
    evidence = evidence_obj.data
    extractor = resolve_extractor(settings)
    config_hash = config_hash_for(extractor.config())

    if requested_facets is None:
        requested_facets = active_profile_facets(
            reader,
            evidence.get("source_category", ""),
            settings.default_profile_facets,
        )
    requested = tuple(sorted(dict.fromkeys(requested_facets)))
    revision_id = evidence["revision_id"]

    run_identity = stable_id(
        "extraction_run",
        revision_id,
        extractor.extractor_id,
        extractor.extractor_version,
        config_hash,
        ",".join(requested),
    )
    existing = next(
        (
            obj
            for obj in reader.objects(type="extraction_run")
            if obj.data.get("run_identity") == run_identity
        ),
        None,
    )
    if existing is not None:
        return {"ok": True, "run": existing, "created": False, "annotations": []}

    if extractor_disabled(reader, extractor.extractor_id, extractor.extractor_version):
        raise RuntimeError(
            "annotation extractor is disabled: "
            f"{extractor.extractor_id}@{extractor.extractor_version}"
        )

    covered = _facets_covered(
        reader, revision_id, extractor.extractor_id,
        extractor.extractor_version, config_hash,
    )
    implemented = set(extractor.implemented_facets())
    cached = tuple(facet for facet in requested if facet in covered)
    missing = tuple(
        facet for facet in requested if facet not in covered and facet in implemented
    )
    unimplemented = tuple(
        facet for facet in requested if facet not in covered and facet not in implemented
    )

    content = evidence.get("normalized_content", "")
    normalized_metadata = evidence.get("normalized_metadata") or {}
    attribution, author_role = _attribution(normalized_metadata)
    observation_time = evidence.get("provider_time")

    drafts = extractor.extract(content, normalized_metadata, missing) if missing else []

    run = graph.add_object(
        "extraction_run",
        {
            "run_identity": run_identity,
            "evidence_id": evidence_obj.id,
            "evidence_identity": evidence["evidence_identity"],
            "revision_id": revision_id,
            "extractor_id": extractor.extractor_id,
            "extractor_version": extractor.extractor_version,
            "config_hash": config_hash,
            "requested_facets": list(requested),
            "executed_facets": list(missing),
            "cached_facets": list(cached),
            "annotation_ids": [],
            "status": "completed",
            "error": None,
            "metadata": {"acquired_at_event_id": evidence.get("acquired_at_event_id")},
        },
    )
    graph.add_relation(run.id, evidence_obj.id, "run_for")

    seen_identities = {
        obj.data.get("annotation_identity")
        for obj in reader.objects(type="semantic_annotation")
        if obj.data.get("revision_id") == revision_id
    }
    annotation_ids: list[str] = []
    annotations = []
    for draft in drafts:
        identity = annotation_identity_for(
            revision_id,
            extractor.extractor_id,
            extractor.extractor_version,
            config_hash,
            draft,
        )
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        annotation = graph.add_object(
            "semantic_annotation",
            {
                "annotation_identity": identity,
                "facet": draft.facet,
                "body": validate_body(draft.facet, draft.body),
                "evidence_id": evidence_obj.id,
                "evidence_identity": evidence["evidence_identity"],
                "revision_id": revision_id,
                "selector": {
                    "kind": "char_span",
                    "start": draft.start,
                    "end": draft.end,
                    "exact": draft.exact,
                },
                "extractor_id": extractor.extractor_id,
                "extractor_version": extractor.extractor_version,
                "config_hash": config_hash,
                "confidence": draft.confidence,
                "attribution": attribution,
                "author_role": author_role,
                "event_time": draft.event_time,
                "observation_time": observation_time,
                "modality": draft.modality,
                "polarity": draft.polarity,
                "status": "active",
                "invalidation_reason": None,
                "run_id": run.id,
                "metadata": dict(draft.metadata),
            },
        )
        graph.add_relation(annotation.id, evidence_obj.id, "annotation_for")
        graph.add_relation(run.id, annotation.id, "produced_annotation")
        annotation_ids.append(annotation.id)
        annotations.append(annotation)

    patch(
        graph,
        run.id,
        {"annotation_ids": annotation_ids},
        rationale="record produced annotations",
    )

    skipped = [{"facet": facet, "reason": "cached"} for facet in cached]
    skipped.extend(
        {"facet": facet, "reason": "not_implemented"} for facet in unimplemented
    )
    coverage = graph.add_object(
        "extraction_coverage",
        {
            "coverage_identity": stable_id("coverage", run_identity),
            "run_id": run.id,
            "evidence_id": evidence_obj.id,
            "revision_id": revision_id,
            "processed_facets": list(missing),
            "skipped_facets": skipped,
            "content_chars_total": len(content),
            "content_chars_processed": min(len(content), settings.max_content_chars),
            "truncated": len(content) > settings.max_content_chars,
            "metadata": {},
        },
    )
    graph.add_relation(coverage.id, run.id, "coverage_for")

    return {
        "ok": True,
        "run": graph.get_object(run.id),
        "created": True,
        "annotations": annotations,
        "coverage_id": coverage.id,
    }


__all__ = [
    "active_profile_facets",
    "annotation_identity_for",
    "extractor_disabled",
    "resolve_extractor",
    "run_annotation_extraction",
    "stable_id",
]
