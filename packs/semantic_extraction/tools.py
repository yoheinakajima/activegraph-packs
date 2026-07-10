"""Owner/host-facing tools for the shared annotation layer."""

from __future__ import annotations

from typing import Any, Optional

from activegraph.packs import tool

from .engine import run_annotation_extraction, stable_id
from .facets import STANDARD_FACETS
from .settings import SemanticExtractionSettings


def extract_annotations_fn(
    graph,
    evidence_id: str,
    *,
    facets: Optional[list[str]] = None,
    settings: Optional[SemanticExtractionSettings] = None,
) -> dict[str, Any]:
    """Explicitly (re-)extract one evidence revision.

    Same engine as the eager behavior, so the cache identity semantics
    are identical: an already-satisfied identity is a no-op; a wider
    facet set fills only what is missing.
    """
    settings = settings or SemanticExtractionSettings()
    evidence_obj = graph.get_object(evidence_id)
    if evidence_obj is None or evidence_obj.type != "activity_evidence":
        raise ValueError(f"no activity_evidence with id {evidence_id!r}")
    requested = tuple(facets) if facets else None
    result = run_annotation_extraction(
        graph, evidence_obj, settings=settings, requested_facets=requested
    )
    run = result["run"]
    return {
        "ok": result["ok"],
        "created": result["created"],
        "run_id": run.id,
        "run_identity": run.data.get("run_identity"),
        "executed_facets": run.data.get("executed_facets"),
        "cached_facets": run.data.get("cached_facets"),
        "annotation_ids": run.data.get("annotation_ids"),
    }


def update_extraction_profile_fn(
    graph,
    *,
    default_facets: Optional[list[str]] = None,
    facets_by_source_category: Optional[dict[str, list[str]]] = None,
    rationale: str = "",
    created_by: str = "owner",
) -> dict[str, Any]:
    """Mint the next extraction_profile version; supersede the active one.

    Config artifacts are versioned and supersedable, never edited in
    place (D042). Unknown facet names fail loud — policy can narrow or
    widen, not invent.
    """
    for facet_list in [default_facets or []] + list(
        (facets_by_source_category or {}).values()
    ):
        for facet in facet_list:
            if facet not in STANDARD_FACETS and "." not in facet:
                raise ValueError(f"unknown facet {facet!r}")

    profiles = sorted(
        graph.objects(type="extraction_profile"),
        key=lambda obj: obj.data.get("version", 0),
    )
    current = next(
        (obj for obj in reversed(profiles) if obj.data.get("status") == "active"),
        None,
    )
    version = (profiles[-1].data["version"] + 1) if profiles else 1
    base = current.data if current is not None else {}
    new_profile = graph.add_object(
        "extraction_profile",
        {
            "profile_identity": stable_id("extraction_profile", version),
            "version": version,
            "status": "active",
            "default_facets": sorted(
                default_facets
                if default_facets is not None
                else base.get("default_facets", [])
            ),
            "facets_by_source_category": (
                {key: sorted(value) for key, value in facets_by_source_category.items()}
                if facets_by_source_category is not None
                else dict(base.get("facets_by_source_category", {}))
            ),
            "created_by": created_by,
            "rationale": rationale,
            "supersedes_profile_id": current.id if current is not None else None,
        },
    )
    if current is not None:
        graph.patch_object(
            current.id,
            {"status": "superseded"},
            rationale=f"superseded by extraction_profile v{version}",
        )
    return {"ok": True, "profile_id": new_profile.id, "version": version}


def invalidate_annotation_extractor_fn(
    graph,
    extractor_id: str,
    extractor_version: str,
    *,
    reason: str,
) -> dict[str, Any]:
    """Demote one extractor version and everything that depends on it.

    Disables the version, invalidates its runs and annotations, and
    walks provenance to demote dependent candidates — profile candidates
    flip to ``invalidated``; memory candidates (which have no status
    field by design — Memory Gateway owns acceptance) are zeroed and
    marked unaccepted. Evidence is never touched (ADR 0014).
    """
    if not reason:
        raise ValueError("an invalidation reason is required")

    graph.add_object(
        "annotation_extractor_state",
        {
            "state_identity": stable_id(
                "annotation_extractor_state", extractor_id, extractor_version, reason
            ),
            "extractor_id": extractor_id,
            "extractor_version": extractor_version,
            "status": "disabled",
            "reason": reason,
        },
    )

    invalidated_runs = 0
    for run in graph.objects(type="extraction_run"):
        if (
            run.data.get("extractor_id") == extractor_id
            and run.data.get("extractor_version") == extractor_version
            and run.data.get("status") == "completed"
        ):
            graph.patch_object(
                run.id, {"status": "invalidated"}, rationale=reason
            )
            invalidated_runs += 1

    invalidated_annotation_ids: set[str] = set()
    for annotation in graph.objects(type="semantic_annotation"):
        if (
            annotation.data.get("extractor_id") == extractor_id
            and annotation.data.get("extractor_version") == extractor_version
            and annotation.data.get("status") == "active"
        ):
            graph.patch_object(
                annotation.id,
                {"status": "invalidated", "invalidation_reason": reason},
                rationale=reason,
            )
            invalidated_annotation_ids.add(annotation.id)

    demoted_profile = 0
    for candidate in graph.objects(type="profile_candidate"):
        metadata = candidate.data.get("metadata") or {}
        if (
            metadata.get("annotation_id") in invalidated_annotation_ids
            and candidate.data.get("status") == "candidate"
        ):
            graph.patch_object(
                candidate.id,
                {"status": "invalidated", "invalidation_reason": reason},
                rationale=reason,
            )
            demoted_profile += 1

    demoted_memory = 0
    for candidate in graph.objects(type="memory_candidate"):
        observation_ids = candidate.data.get("observation_ids") or []
        if invalidated_annotation_ids.intersection(observation_ids):
            graph.patch_object(
                candidate.id,
                {"accepted": False, "confidence": 0.0},
                rationale=reason,
            )
            demoted_memory += 1

    return {
        "ok": True,
        "invalidated_runs": invalidated_runs,
        "invalidated_annotations": len(invalidated_annotation_ids),
        "demoted_profile_candidates": demoted_profile,
        "demoted_memory_candidates": demoted_memory,
    }


def annotation_coverage_fn(
    graph, *, evidence_id: Optional[str] = None
) -> list[dict[str, Any]]:
    """Coverage records, queryable — the completeness ledger."""
    records = []
    for coverage in graph.objects(type="extraction_coverage"):
        if evidence_id is not None and coverage.data.get("evidence_id") != evidence_id:
            continue
        records.append({"coverage_id": coverage.id, **coverage.data})
    records.sort(key=lambda record: record["coverage_identity"])
    return records


@tool(
    name="extract_annotations",
    description=(
        "Run (or cache-hit) the configured annotation extractor over one "
        "evidence revision, optionally for an explicit facet set."
    ),
)
def extract_annotations(
    graph, evidence_id: str = "", facets: Optional[list[str]] = None
) -> dict[str, Any]:
    return extract_annotations_fn(graph, evidence_id, facets=facets)


@tool(
    name="update_extraction_profile",
    description=(
        "Mint the next version of the extraction_profile config artifact "
        "(owner-editable facet policy per source category)."
    ),
)
def update_extraction_profile(
    graph,
    default_facets: Optional[list[str]] = None,
    facets_by_source_category: Optional[dict[str, list[str]]] = None,
    rationale: str = "",
    created_by: str = "owner",
) -> dict[str, Any]:
    return update_extraction_profile_fn(
        graph,
        default_facets=default_facets,
        facets_by_source_category=facets_by_source_category,
        rationale=rationale,
        created_by=created_by,
    )


@tool(
    name="invalidate_annotation_extractor",
    description=(
        "Disable one annotation-extractor version and demote its "
        "annotations and dependent candidates via provenance."
    ),
)
def invalidate_annotation_extractor(
    graph, extractor_id: str = "", extractor_version: str = "", reason: str = ""
) -> dict[str, Any]:
    return invalidate_annotation_extractor_fn(
        graph, extractor_id, extractor_version, reason=reason
    )


@tool(
    name="annotation_coverage",
    description="List extraction coverage records, optionally per evidence.",
)
def annotation_coverage(graph, evidence_id: str = "") -> list[dict[str, Any]]:
    return annotation_coverage_fn(graph, evidence_id=evidence_id or None)


TOOLS = [
    extract_annotations,
    update_extraction_profile,
    invalidate_annotation_extractor,
    annotation_coverage,
]

__all__ = [
    "TOOLS",
    "annotation_coverage_fn",
    "extract_annotations_fn",
    "invalidate_annotation_extractor_fn",
    "update_extraction_profile_fn",
]
