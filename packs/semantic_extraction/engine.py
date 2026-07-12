"""The one extraction implementation shared by the behavior and the tools.

Behaviors receive the constrained BehaviorGraph and pass ``read_view``;
tools receive the full Graph and pass it for both. Either way the write
surface is ``graph`` and the cache lookups go through ``reader`` — the
logic is identical, so cache identity semantics cannot drift between
the eager path and explicit re-extraction.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
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


def parse_extractor_ref(ref: str) -> tuple[str, str]:
    """Split an ``extractor_id@version`` reference; fail loud on junk."""
    extractor_id, sep, version = ref.partition("@")
    if not sep or not extractor_id or not version:
        raise ValueError(
            f"extractor reference {ref!r} must be '<extractor_id>@<version>'"
        )
    return extractor_id, version


def resolve_extractor(
    settings: SemanticExtractionSettings, ref: Optional[str] = None
) -> AnnotationExtractor:
    """Build one extractor: the settings default, or an explicit ref.

    The deterministic default is constructed from settings so its config
    hash reflects the live bounds; ``semantic.llm`` is constructed from
    settings plus the configured provider (and registered at the seam);
    any other id/version comes from the registry.
    """
    if ref is None:
        extractor_id = settings.extractor_id
        extractor_version = settings.extractor_version
    else:
        extractor_id, extractor_version = parse_extractor_ref(ref)

    default = DeterministicExtractorV1()
    if (
        extractor_id == default.extractor_id
        and extractor_version == default.extractor_version
    ):
        return DeterministicExtractorV1(
            max_content_chars=settings.max_content_chars,
            max_annotations_per_facet=settings.max_annotations_per_facet,
            min_assertion_chars=settings.min_assertion_chars,
            topic_tag_count=settings.topic_tag_count,
        )
    if extractor_id == "semantic.llm" and extractor_version == "0.1.0":
        from .extractor import register_annotation_extractor
        from .llm_extractor import build_llm_extractor

        extractor = build_llm_extractor(settings)
        register_annotation_extractor(extractor, replace=True)
        return extractor
    return get_annotation_extractor(extractor_id, extractor_version)


def _active_profile(reader):
    profiles = [
        obj
        for obj in reader.objects(type="extraction_profile")
        if obj.data.get("status") == "active"
    ]
    if not profiles:
        return None
    profiles.sort(key=lambda obj: obj.data.get("version", 0))
    return profiles[-1]


def active_profile_facets(
    reader, source_category: str, fallback: tuple[str, ...]
) -> tuple[str, ...]:
    """Requested facets for one source category, from the config artifact.

    The latest ``active`` extraction_profile decides; the settings floor
    is only the fallback for graphs where no profile exists yet.
    """
    profile = _active_profile(reader)
    if profile is None:
        return tuple(sorted(fallback))
    data = profile.data
    per_category = data.get("facets_by_source_category") or {}
    facets = per_category.get(source_category) or data.get("default_facets") or []
    return tuple(sorted(facets)) if facets else tuple(sorted(fallback))


def active_profile_extractor_map(reader) -> dict[str, str]:
    """facet → ``extractor_id@version`` from the active profile.

    Facets absent from the map run on the settings default extractor.
    An empty map (or no profile) is exactly today's single-extractor
    behavior — byte-identical.
    """
    profile = _active_profile(reader)
    if profile is None:
        return {}
    return dict(profile.data.get("extractor_by_facet") or {})


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
    selection_id: Optional[str],
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
            and data.get("selection_id") == selection_id
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
    extractor_ref: Optional[str] = None,
    selection_id: Optional[str] = None,
    content_segments: Optional[list[dict[str, Any]]] = None,
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
    extractor = resolve_extractor(settings, extractor_ref)
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
        selection_id or "full_evidence",
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
        extractor.extractor_version, config_hash, selection_id,
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

    selected_chars = len(content)
    if content_segments is not None:
        verified_segments: list[tuple[int, str]] = []
        selected_chars = 0
        for segment in content_segments:
            start = int(segment.get("start", -1))
            end = int(segment.get("end", -1))
            if start < 0 or end < start or end > len(content):
                raise ValueError("selection span is outside authoritative evidence")
            exact = content[start:end]
            if hashlib.sha256(exact.encode("utf-8")).hexdigest() != segment.get("exact_hash"):
                raise ValueError("selection hash does not match authoritative evidence")
            verified_segments.append((start, exact))
            selected_chars += len(exact)
        drafts = []
        per_facet: dict[str, int] = {}
        if missing:
            for offset, exact in verified_segments:
                for draft in extractor.extract(exact, normalized_metadata, missing):
                    count = per_facet.get(draft.facet, 0)
                    if count >= settings.max_annotations_per_facet:
                        continue
                    per_facet[draft.facet] = count + 1
                    drafts.append(
                        replace(
                            draft,
                            start=draft.start + offset,
                            end=draft.end + offset,
                            metadata={**dict(draft.metadata), "selection_id": selection_id},
                        )
                    )
    else:
        drafts = extractor.extract(content, normalized_metadata, missing) if missing else []

    run = graph.add_object(
        "extraction_run",
        {
            "run_identity": run_identity,
            "evidence_id": evidence_obj.id,
            "evidence_identity": evidence["evidence_identity"],
            "revision_id": revision_id,
            "selection_id": selection_id,
            "extractor_id": extractor.extractor_id,
            "extractor_version": extractor.extractor_version,
            "config_hash": config_hash,
            "requested_facets": list(requested),
            "executed_facets": list(missing),
            "cached_facets": list(cached),
            "annotation_ids": [],
            "status": "completed",
            "error": None,
            "metadata": {
                "acquired_at_event_id": evidence.get("acquired_at_event_id"),
                "selection_count": len(content_segments or []),
            },
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
                "metadata": {
                    **dict(draft.metadata),
                    "source_category": evidence.get("source_category", ""),
                    "subject_scope": normalized_metadata.get("subject_scope"),
                    "profile_candidate_eligible": (
                        normalized_metadata.get("subject_scope") == "owner_profile"
                    ),
                    "memory_candidate_eligible": (
                        evidence.get("source_category") != "communication"
                        or normalized_metadata.get("subject_scope") == "owner_profile"
                    ),
                },
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
            "content_chars_processed": min(selected_chars, settings.max_content_chars),
            "truncated": selected_chars > settings.max_content_chars,
            "metadata": {"selection_id": selection_id},
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


def run_profile_extraction(
    graph,
    evidence_obj,
    *,
    settings: SemanticExtractionSettings,
    requested_facets: Optional[tuple[str, ...]] = None,
    reader=None,
    selection_id: Optional[str] = None,
    content_segments: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Extract per the active profile's facet AND extractor policy.

    The profile's ``extractor_by_facet`` map partitions the requested
    facets into extractor groups; each group is one cache-identified
    ``run_annotation_extraction`` pass. With an empty map (no provider
    configured / today's seeded profile) this is exactly one pass on the
    settings default extractor — byte-identical to the previous
    single-extractor path.
    """
    reader = reader or graph
    evidence = evidence_obj.data
    if requested_facets is None:
        requested_facets = active_profile_facets(
            reader,
            evidence.get("source_category", ""),
            settings.default_profile_facets,
        )
    requested = tuple(sorted(dict.fromkeys(requested_facets)))
    extractor_map = active_profile_extractor_map(reader)

    groups: dict[Optional[str], list[str]] = {}
    for facet in requested:
        groups.setdefault(extractor_map.get(facet), []).append(facet)

    results = []
    # The default group first, then explicit refs in sorted order —
    # deterministic run ordering regardless of dict insertion history.
    ordered_refs = sorted(
        (ref for ref in groups if ref is not None)
    )
    for ref in [None, *ordered_refs]:
        facets = groups.get(ref)
        if not facets:
            continue
        results.append(
            run_annotation_extraction(
                graph,
                evidence_obj,
                settings=settings,
                requested_facets=tuple(facets),
                reader=reader,
                extractor_ref=ref,
                selection_id=selection_id,
                content_segments=content_segments,
            )
        )
    return results


__all__ = [
    "active_profile_extractor_map",
    "active_profile_facets",
    "annotation_identity_for",
    "extractor_disabled",
    "parse_extractor_ref",
    "resolve_extractor",
    "run_annotation_extraction",
    "run_profile_extraction",
    "stable_id",
]
