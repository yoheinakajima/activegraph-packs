"""Host-driven selection-request execution (ADR 0041).

With ``deferred_selection_execution`` on, the minting behavior leaves
``selection_extraction_request`` objects ``proposed`` and a host pump runs:

    engine thread   prepare_selection_request_fn(graph, request_id, ...)
    worker thread   perform_selection_request(prepared, settings)   # network
    engine thread   commit_selection_request_fn(...) / fail_selection_request_fn(...)

Every request/settlement transition here mirrors the synchronous behavior in
``behaviors.extract_selected_evidence`` exactly — same patches, same
``semantic.selection_extraction_settled`` event — so downstream consumers
cannot tell which path ran.
"""

from __future__ import annotations

from typing import Any, Optional

from activegraph import Event

from .engine import (
    PreparedExtraction,
    commit_prepared_extraction,
    extraction_groups,
    patch,
    perform_prepared_extraction,
    prepare_annotation_extraction,
)
from .settings import SemanticExtractionSettings


def _emit(graph, event_type: str, payload: dict[str, Any]) -> None:
    if not hasattr(graph, "ids"):
        graph.emit(event_type, payload)
        return
    graph.emit(Event(
        id=graph.ids.event(), type=event_type, payload=payload,
        actor="semantic_extraction.deferred", timestamp=graph.clock.now(),
    ))


def _settle_failed(graph, request_id: str, error: str) -> dict[str, Any]:
    patch(
        graph, request_id,
        {"status": "failed", "error": error[:500]},
        rationale="deferred selection extraction failed",
    )
    _emit(graph, "semantic.selection_extraction_settled", {
        "request_id": request_id, "status": "failed", "run_ids": [],
        "annotation_ids": [], "error": error[:500],
    })
    return {"status": "failed", "request_id": request_id, "error": error[:500]}


def pending_selection_requests_fn(reader) -> list[str]:
    """Proposed request ids, oldest-first by object id (stable order)."""
    rows = [
        obj for obj in reader.objects(type="selection_extraction_request")
        if obj.data.get("status") == "proposed"
    ]
    rows.sort(key=lambda obj: obj.id)
    return [obj.id for obj in rows]


def prepare_selection_request_fn(
    graph,
    request_id: str,
    *,
    settings: SemanticExtractionSettings,
    reader=None,
) -> dict[str, Any]:
    """Phase 1, engine thread: validate the request and stage every group."""
    reader = reader or graph
    request = graph.get_object(request_id)
    if request is None or request.type != "selection_extraction_request":
        return {"status": "skipped", "request_id": request_id, "reason": "missing"}
    data = dict(request.data or {})
    if data.get("status") != "proposed":
        return {"status": "skipped", "request_id": request_id, "reason": data.get("status")}
    evidence = graph.get_object(str(data.get("evidence_id") or ""))
    if evidence is None or evidence.type != "activity_evidence":
        return _settle_failed(graph, request_id, "evidence_not_found")
    if evidence.data.get("revision_id") != data.get("revision_id"):
        return _settle_failed(graph, request_id, "revision_mismatch")

    requested = tuple(data.get("requested_facets") or [])
    selection_id = str(data.get("selection_id") or "")
    segments = list(data.get("selections") or [])
    groups, deopt_by_ref = extraction_groups(
        graph, evidence, settings=settings, requested_facets=requested, reader=reader
    )
    prepared_groups: list[dict[str, Any]] = []
    ordered_refs = sorted(ref for ref in groups if ref is not None)
    try:
        for ref in [None, *ordered_refs]:
            facets = groups.get(ref)
            if not facets:
                continue
            prepared = prepare_annotation_extraction(
                evidence,
                settings=settings,
                requested_facets=tuple(facets),
                reader=reader,
                extractor_ref=ref,
                selection_id=selection_id,
                content_segments=segments,
            )
            prepared_groups.append({
                "prepared": prepared,
                "deopt_ids": list(deopt_by_ref.get(str(ref), [])),
            })
    except Exception as exc:
        return _settle_failed(graph, request_id, f"{type(exc).__name__}: {exc}")
    return {
        "status": "prepared",
        "request_id": request_id,
        "groups": prepared_groups,
    }


def perform_selection_request(
    prepared_groups: list[dict[str, Any]],
    *,
    settings: SemanticExtractionSettings,
) -> list[Optional[list[Any]]]:
    """Phase 2, worker thread: provider calls only — zero graph access."""
    drafts_per_group: list[Optional[list[Any]]] = []
    for group in prepared_groups:
        prepared: PreparedExtraction = group["prepared"]
        if prepared.cached_run is not None:
            drafts_per_group.append(None)
            continue
        drafts_per_group.append(
            perform_prepared_extraction(prepared, settings=settings)
        )
    return drafts_per_group


def commit_selection_request_fn(
    graph,
    request_id: str,
    prepared_groups: list[dict[str, Any]],
    drafts_per_group: list[Optional[list[Any]]],
    *,
    settings: SemanticExtractionSettings,
    reader=None,
) -> dict[str, Any]:
    """Phase 3, engine thread: commit runs, settle the request, emit."""
    reader = reader or graph
    results: list[dict[str, Any]] = []
    for group, drafts in zip(prepared_groups, drafts_per_group):
        prepared: PreparedExtraction = group["prepared"]
        if prepared.cached_run is not None:
            result = {
                "ok": True, "run": prepared.cached_run,
                "created": False, "annotations": [],
            }
        else:
            result = commit_prepared_extraction(
                graph, prepared, list(drafts or []),
                settings=settings, reader=reader,
            )
        results.append(result)
        for deopt_id in group.get("deopt_ids") or []:
            patch(
                graph, deopt_id,
                {
                    "fallback_status": "completed" if result.get("ok") else "failed",
                    "fallback_run_ids": [result["run"].id],
                },
                rationale="record dynamic reference fallback",
            )
    run_ids = [result["run"].id for result in results]
    annotation_ids = [
        annotation.id
        for result in results
        for annotation in result.get("annotations", [])
    ]
    patch(
        graph, request_id,
        {
            "status": "completed",
            "run_ids": run_ids,
            "annotation_ids": annotation_ids,
            "error": None,
        },
        rationale="deferred selection extraction settled",
    )
    _emit(graph, "semantic.selection_extraction_settled", {
        "request_id": request_id, "status": "completed",
        "run_ids": run_ids, "annotation_ids": annotation_ids, "error": None,
    })
    return {
        "status": "completed",
        "request_id": request_id,
        "run_ids": run_ids,
        "annotation_ids": annotation_ids,
    }


def fail_selection_request_fn(graph, request_id: str, error: str) -> dict[str, Any]:
    """Host-visible failure path for a perform-phase exception."""
    return _settle_failed(graph, request_id, error)


__all__ = [
    "commit_selection_request_fn",
    "fail_selection_request_fn",
    "pending_selection_requests_fn",
    "perform_selection_request",
    "prepare_selection_request_fn",
]
