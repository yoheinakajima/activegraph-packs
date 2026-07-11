"""Owner/host-facing tools for the shared annotation layer."""

from __future__ import annotations

from typing import Any, Optional

from activegraph.packs import tool

from .engine import (
    parse_extractor_ref,
    resolve_extractor,
    run_profile_extraction,
    stable_id,
)
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
    facet set fills only what is missing. The active profile's
    ``extractor_by_facet`` map decides which extractor serves which
    facet — one cache-identified run per extractor group.
    """
    settings = settings or SemanticExtractionSettings()
    evidence_obj = graph.get_object(evidence_id)
    if evidence_obj is None or evidence_obj.type != "activity_evidence":
        raise ValueError(f"no activity_evidence with id {evidence_id!r}")
    requested = tuple(facets) if facets else None
    results = run_profile_extraction(
        graph, evidence_obj, settings=settings, requested_facets=requested
    )
    runs = [result["run"] for result in results]
    executed = sorted(
        {facet for run in runs for facet in run.data.get("executed_facets", [])}
    )
    cached = sorted(
        {facet for run in runs for facet in run.data.get("cached_facets", [])}
    )
    annotation_ids = [
        annotation_id
        for run in runs
        for annotation_id in run.data.get("annotation_ids", [])
    ]
    first = runs[0]
    return {
        "ok": all(result["ok"] for result in results),
        "created": any(result["created"] for result in results),
        "run_id": first.id,
        "run_identity": first.data.get("run_identity"),
        "executed_facets": executed,
        "cached_facets": cached,
        "annotation_ids": annotation_ids,
        "runs": [
            {
                "run_id": run.id,
                "run_identity": run.data.get("run_identity"),
                "extractor_id": run.data.get("extractor_id"),
                "extractor_version": run.data.get("extractor_version"),
                "executed_facets": run.data.get("executed_facets"),
                "cached_facets": run.data.get("cached_facets"),
            }
            for run in runs
        ],
    }


def update_extraction_profile_fn(
    graph,
    *,
    default_facets: Optional[list[str]] = None,
    facets_by_source_category: Optional[dict[str, list[str]]] = None,
    extractor_by_facet: Optional[dict[str, str]] = None,
    rationale: str = "",
    created_by: str = "owner",
) -> dict[str, Any]:
    """Mint the next extraction_profile version; supersede the active one.

    Config artifacts are versioned and supersedable, never edited in
    place (D042). Unknown facet names and malformed extractor references
    fail loud — policy can narrow or widen, not invent.
    """
    for facet_list in [default_facets or []] + list(
        (facets_by_source_category or {}).values()
    ):
        for facet in facet_list:
            if facet not in STANDARD_FACETS and "." not in facet:
                raise ValueError(f"unknown facet {facet!r}")
    for facet, ref in (extractor_by_facet or {}).items():
        if facet not in STANDARD_FACETS and "." not in facet:
            raise ValueError(f"unknown facet {facet!r}")
        parse_extractor_ref(ref)

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
            "extractor_by_facet": (
                dict(extractor_by_facet)
                if extractor_by_facet is not None
                else dict(base.get("extractor_by_facet", {}))
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

    # The activity_normalizer compatibility projectors (ADR 0026 step 2)
    # mint these from activity.* annotations; same demotion rule.
    demoted_compat = 0
    for candidate_type in (
        "preference_candidate", "task_candidate",
        "skill_candidate", "eval_candidate",
    ):
        try:
            candidates = graph.objects(type=candidate_type)
        except Exception:
            continue  # type not registered in this graph
        for candidate in candidates:
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
                demoted_compat += 1

    return {
        "ok": True,
        "invalidated_runs": invalidated_runs,
        "invalidated_annotations": len(invalidated_annotation_ids),
        "demoted_profile_candidates": demoted_profile,
        "demoted_memory_candidates": demoted_memory,
        "demoted_compat_candidates": demoted_compat,
    }


def run_extractor_trial_fn(
    graph,
    evidence_ids: list[str],
    *,
    candidate_ref: str = "semantic.llm@0.1.0",
    baseline_ref: str = "semantic.deterministic@0.1.0",
    facets: Optional[list[str]] = None,
    settings: Optional[SemanticExtractionSettings] = None,
    created_by: str = "owner",
) -> dict[str, Any]:
    """Fork-trial the candidate extractor against the baseline (ADR 0014).

    Both extractors run over the same recorded evidence content — drafts
    only, no annotations are materialized and no policy changes. The
    per-facet comparison lands as an ``extractor_promotion_evidence``
    object: the shape an explicit promotion cites.
    """
    settings = settings or SemanticExtractionSettings()
    candidate = resolve_extractor(settings, candidate_ref)
    baseline = resolve_extractor(settings, baseline_ref)
    trial_facets = tuple(
        sorted(facets or set(candidate.implemented_facets()))
    )
    for facet in trial_facets:
        if facet not in STANDARD_FACETS and "." not in facet:
            raise ValueError(f"unknown facet {facet!r}")

    evidence_objs = []
    for evidence_id in evidence_ids:
        obj = graph.get_object(evidence_id)
        if obj is None or obj.type != "activity_evidence":
            raise ValueError(f"no activity_evidence with id {evidence_id!r}")
        evidence_objs.append(obj)
    if not evidence_objs:
        raise ValueError("an extractor trial needs at least one evidence id")

    def _keyed(drafts):
        return {
            (draft.facet, draft.start, draft.end, draft.exact)
            for draft in drafts
        }

    comparison: dict[str, dict[str, int]] = {
        facet: {
            "baseline": 0,
            "candidate": 0,
            "candidate_only": 0,
            "baseline_only": 0,
        }
        for facet in trial_facets
    }
    for obj in evidence_objs:
        content = obj.data.get("normalized_content", "")
        metadata = obj.data.get("normalized_metadata") or {}
        baseline_keys = _keyed(baseline.extract(content, metadata, trial_facets))
        candidate_keys = _keyed(candidate.extract(content, metadata, trial_facets))
        for facet in trial_facets:
            base_f = {key for key in baseline_keys if key[0] == facet}
            cand_f = {key for key in candidate_keys if key[0] == facet}
            entry = comparison[facet]
            entry["baseline"] += len(base_f)
            entry["candidate"] += len(cand_f)
            entry["candidate_only"] += len(cand_f - base_f)
            entry["baseline_only"] += len(base_f - cand_f)

    # Verdict is facet-coverage-level: which extractor produces facets
    # the other cannot. Span-level differences within a facet both
    # extractors serve stay in the comparison detail but don't decide —
    # different extractors legitimately anchor the same facts on
    # different spans.
    facet_gains = sum(
        1
        for entry in comparison.values()
        if entry["candidate"] > 0 and entry["baseline"] == 0
    )
    facet_losses = sum(
        1
        for entry in comparison.values()
        if entry["baseline"] > 0 and entry["candidate"] == 0
    )
    if facet_gains > facet_losses:
        verdict = "candidate_richer"
    elif facet_losses > facet_gains:
        verdict = "baseline_richer"
    else:
        verdict = "neutral"

    candidate_id, candidate_version = parse_extractor_ref(candidate_ref)
    baseline_id, baseline_version = parse_extractor_ref(baseline_ref)
    evidence = graph.add_object(
        "extractor_promotion_evidence",
        {
            "evidence_identity": stable_id(
                "extractor_trial",
                candidate_ref,
                baseline_ref,
                ",".join(sorted(obj.data["revision_id"] for obj in evidence_objs)),
                ",".join(trial_facets),
            ),
            "candidate_extractor_id": candidate_id,
            "candidate_extractor_version": candidate_version,
            "baseline_extractor_id": baseline_id,
            "baseline_extractor_version": baseline_version,
            "evidence_ids": [obj.id for obj in evidence_objs],
            "facets": list(trial_facets),
            "comparison": comparison,
            "verdict": verdict,
            "rationale": (
                "recorded-content trial: candidate vs baseline drafts, "
                "keyed by (facet, start, end, exact)"
            ),
            "created_by": created_by,
            "metadata": {
                "candidate_config": candidate.config(),
                "baseline_config": baseline.config(),
            },
        },
    )
    return {
        "ok": True,
        "evidence_id": evidence.id,
        "verdict": verdict,
        "comparison": comparison,
    }


def promote_llm_extractor_fn(
    graph,
    promotion_evidence_id: str,
    *,
    approver: str,
    facets: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Promote the candidate extractor citing recorded trial evidence.

    ADR 0014: nothing promotes without evidence plus an explicit,
    named approval. Promotion flips the extractor's state to
    ``promoted`` and mints the next profile version routing the given
    facets (default: every facet the trial covered) to the candidate.
    """
    if not approver:
        raise ValueError("promotion requires an explicit approver")
    evidence = graph.get_object(promotion_evidence_id)
    if evidence is None or evidence.type != "extractor_promotion_evidence":
        raise ValueError(
            f"no extractor_promotion_evidence with id {promotion_evidence_id!r}"
        )
    data = evidence.data
    ref = f"{data['candidate_extractor_id']}@{data['candidate_extractor_version']}"
    routed = sorted(facets or data.get("facets") or [])
    if not routed:
        raise ValueError("nothing to route: the trial covered no facets")

    graph.add_object(
        "annotation_extractor_state",
        {
            "state_identity": stable_id(
                "annotation_extractor_state", ref, "promoted", promotion_evidence_id
            ),
            "extractor_id": data["candidate_extractor_id"],
            "extractor_version": data["candidate_extractor_version"],
            "status": "promoted",
            "reason": f"promoted by {approver} citing {promotion_evidence_id}",
            "metadata": {"promotion_evidence_id": promotion_evidence_id},
        },
    )
    current = next(
        (
            obj
            for obj in sorted(
                graph.objects(type="extraction_profile"),
                key=lambda item: item.data.get("version", 0),
                reverse=True,
            )
            if obj.data.get("status") == "active"
        ),
        None,
    )
    merged = dict(current.data.get("extractor_by_facet", {})) if current else {}
    merged.update({facet: ref for facet in routed})
    profile = update_extraction_profile_fn(
        graph,
        extractor_by_facet=merged,
        rationale=(
            f"promote {ref} for {', '.join(routed)} "
            f"(evidence {promotion_evidence_id}, approved by {approver})"
        ),
        created_by=approver,
    )
    return {"ok": True, "extractor_ref": ref, "routed_facets": routed, **profile}


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


@tool(
    name="run_extractor_trial",
    description=(
        "Fork-trial a candidate extractor against the baseline on recorded "
        "evidence content; records the per-facet comparison as promotion "
        "evidence (ADR 0014). Changes no policy."
    ),
)
def run_extractor_trial(
    graph,
    evidence_ids: Optional[list[str]] = None,
    candidate_ref: str = "semantic.llm@0.1.0",
    baseline_ref: str = "semantic.deterministic@0.1.0",
    facets: Optional[list[str]] = None,
) -> dict[str, Any]:
    return run_extractor_trial_fn(
        graph,
        evidence_ids or [],
        candidate_ref=candidate_ref,
        baseline_ref=baseline_ref,
        facets=facets,
    )


@tool(
    name="promote_llm_extractor",
    description=(
        "Promote the candidate extractor for the trialed facets, citing "
        "recorded promotion evidence and a named approver (ADR 0014)."
    ),
)
def promote_llm_extractor(
    graph,
    promotion_evidence_id: str = "",
    approver: str = "",
    facets: Optional[list[str]] = None,
) -> dict[str, Any]:
    return promote_llm_extractor_fn(
        graph, promotion_evidence_id, approver=approver, facets=facets
    )


TOOLS = [
    extract_annotations,
    update_extraction_profile,
    invalidate_annotation_extractor,
    annotation_coverage,
    run_extractor_trial,
    promote_llm_extractor,
]

__all__ = [
    "TOOLS",
    "annotation_coverage_fn",
    "extract_annotations_fn",
    "invalidate_annotation_extractor_fn",
    "promote_llm_extractor_fn",
    "run_extractor_trial_fn",
    "update_extraction_profile_fn",
]
