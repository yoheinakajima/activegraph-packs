"""Explicit Gmail -> connector-control adapter.

Gmail owns provider/cursor semantics. This module is the only place that maps
those semantics into the service-neutral control plane; neither the neutral
pack nor BabyAGI parses a Gmail run.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from packs.connector_control.tools import (
    record_connector_binding_fn,
    record_connector_learning_delta_fn,
    record_connector_native_view_fn,
    record_connector_run_observation_fn,
)


_STATE = {
    "proposed": "queued",
    "running": "running",
    "completed": "succeeded",
    "partial": "partial",
    "failed": "failed",
}


def _objects(reader, object_type: str):
    return list(reader.objects(type=object_type))


def _cursor(reader, source_surface_id: str):
    return next(
        (
            obj for obj in _objects(reader, "backfill_cursor")
            if obj.data.get("source_surface_id") == source_surface_id
        ),
        None,
    )


def _evidence_for_run(reader, run_id: str):
    return [
        obj for obj in _objects(reader, "activity_evidence")
        if ((obj.data.get("normalized_metadata") or {}).get("connector_run_id") == run_id)
    ]


def _learning_counts(reader, run) -> dict[str, Any]:
    evidence = _evidence_for_run(reader, run.id)
    evidence_ids = {obj.id for obj in evidence}
    created = sum(1 for obj in evidence if int(obj.data.get("revision_number") or 0) == 1)
    updated = sum(1 for obj in evidence if int(obj.data.get("revision_number") or 0) > 1)

    annotations = [
        obj for obj in _objects(reader, "semantic_annotation")
        if obj.data.get("evidence_id") in evidence_ids and obj.data.get("status") == "active"
    ]
    facets = Counter(str(obj.data.get("facet") or "unknown") for obj in annotations)

    candidates: dict[str, dict[str, int]] = {}
    for object_type in (
        "preference_candidate", "task_candidate", "profile_candidate",
        "skill_candidate", "eval_candidate",
    ):
        rows = [
            obj for obj in _objects(reader, object_type)
            if obj.data.get("evidence_id") in evidence_ids
        ]
        if not rows:
            continue
        outcomes = Counter()
        for obj in rows:
            status = str(obj.data.get("status") or "candidate")
            if status in {"promoted", "accepted"}:
                outcomes["promoted"] += 1
            elif status in {"rejected", "demoted"}:
                outcomes["rejected"] += 1
            elif status in {"invalidated", "suppressed"}:
                outcomes["suppressed"] += 1
            else:
                outcomes["proposed"] += 1
        candidates[object_type] = dict(outcomes)

    failures = [
        obj for obj in _objects(reader, "ingestion_failure")
        if (obj.data.get("metadata") or {}).get("run_id") == run.id
    ]
    refs = [run.id, *[obj.id for obj in evidence], *[obj.id for obj in annotations]]
    return {
        "evidence": {
            "created": created,
            "updated": updated,
            "deleted": int(run.data.get("tombstones_recorded") or 0),
        },
        "annotation_coverage": dict(facets),
        "resolutions": {},
        "candidates": candidates,
        "failures": len(failures),
        "exceptions": [
            {
                "object_id": obj.id,
                "code": str(obj.data.get("error_code") or "unknown"),
                "stage": str(obj.data.get("stage") or "unknown"),
            }
            for obj in failures
        ],
        "refs": refs,
    }


def gmail_learning_settled(reader, run) -> bool:
    """Whether every imported evidence revision has extraction coverage.

    The adapter uses this batch boundary instead of refreshing its aggregate
    once per annotation. It is provider-neutral in shape but Gmail-owned in
    run semantics: ``messages_imported`` is authoritative for this service.
    """

    expected = int((run.data or {}).get("messages_imported") or 0)
    if expected == 0:
        return True
    evidence = _evidence_for_run(reader, run.id)
    if len(evidence) < expected:
        return False
    evidence_ids = {obj.id for obj in evidence}
    covered = {
        str(obj.data.get("evidence_id") or "")
        for obj in _objects(reader, "extraction_coverage")
        if obj.data.get("evidence_id") in evidence_ids
    }
    covered.update(
        str(obj.data.get("evidence_id") or "")
        for obj in _objects(reader, "conversation_interpretation_run")
        if obj.data.get("evidence_id") in evidence_ids
        and obj.data.get("status") in {
            "deterministic_only", "completed", "held", "suppressed"
        }
    )
    return evidence_ids <= covered


def adapt_gmail_run_fn(
    graph,
    run,
    *,
    source_event_id: Optional[str],
    attempt: bool,
    reader,
    native_data: Optional[dict[str, Any]] = None,
    learning_settled_override: Optional[bool] = None,
) -> None:
    data = run.data or {}
    source_surface_id = str(data.get("source_surface_id") or "")
    account_ref = str(data.get("account_ref") or "")
    route = str((data.get("metadata") or {}).get("route") or "composio")
    status = str(data.get("status") or "proposed")
    state = _STATE.get(status, "failed")
    mode = str(data.get("mode") or "backfill")
    cursor = _cursor(reader, source_surface_id)
    cursor_data = dict(cursor.data) if cursor is not None else {}
    has_position = bool(
        cursor_data.get("watermark_ref")
        or cursor_data.get("newest_ingested_ref")
        or cursor_data.get("oldest_ingested_ref")
    )
    position_kind = (
        "history" if cursor_data.get("watermark_ref") else
        "message" if has_position else None
    )
    coverage = (
        "current" if mode == "poll" and state == "succeeded" else
        "bounded" if state in {"succeeded", "partial"} else
        "unknown"
    )
    maintenance_mode = "manual"
    manual_refresh = state in {"succeeded", "partial", "failed"}

    record_connector_binding_fn(
        graph,
        source_surface_id=source_surface_id,
        service="gmail",
        account_ref=account_ref,
        family="conversation",
        active_route=route,
        routes=[route],
        domain_run_type="gmail_sync_run",
        maintenance_mode=maintenance_mode,
        manual_refresh_available=manual_refresh,
        metadata={"adapter": "gmail.control_plane@0.1.0"},
        reader=reader,
    )
    phase = {
        "queued": "queued",
        "running": "checking_updates" if mode == "poll" else "acquiring",
        "succeeded": "served",
        "partial": "sample_ready",
        "failed": "failed",
    }[state]
    record_connector_run_observation_fn(
        graph,
        domain_run_id=run.id,
        source_surface_id=source_surface_id,
        service="gmail",
        account_ref=account_ref,
        family="conversation",
        route=route,
        state=state,
        phase=phase,
        mode=mode,
        source_event_id=source_event_id,
        attempt=attempt,
        bounds={
            "max_items": int(data.get("max_messages") or 0),
            "max_pages": int(data.get("max_pages") or 0),
            "page_size": int(data.get("page_size") or 0),
        },
        counts={
            "imported": int(data.get("messages_imported") or 0),
            "pages": int(data.get("pages_completed") or 0),
            "deleted": len(data.get("deleted_message_ids") or []),
            "tombstones": int(data.get("tombstones_recorded") or 0),
        },
        cursor={
            "position_kind": position_kind,
            "has_position": has_position,
            "advanced": has_position,
            "coverage": coverage,
        },
        maintenance_mode=maintenance_mode,
        manual_refresh_available=manual_refresh,
        next_sync_available=state in {"succeeded", "partial"},
        error_code=data.get("error_code"),
        error=data.get("error"),
        metadata={"adapter": "gmail.control_plane@0.1.0"},
        reader=reader,
    )

    learning = _learning_counts(reader, run)
    settled = (
        gmail_learning_settled(reader, run)
        if learning_settled_override is None else learning_settled_override
    )
    delta_status = {
        "queued": "collecting", "running": "collecting",
        "succeeded": "complete", "partial": "partial", "failed": "failed",
    }[state]
    if state in {"succeeded", "partial"} and not settled:
        delta_status = "collecting"
    record_connector_learning_delta_fn(
        graph,
        domain_run_id=run.id,
        source_surface_id=source_surface_id,
        service="gmail",
        family="conversation",
        status=delta_status,
        **learning,
        metadata={"adapter": "gmail.control_plane@0.1.0"},
        reader=reader,
    )

    # Push 1 proves the native family contract and honest partial/empty states.
    # Push 3 fills threads/messages from the communication family.
    native_state = (
        "failed" if state == "failed" else
        "empty" if int(data.get("messages_imported") or 0) == 0 else
        "ready" if native_data and native_data.get("total_count") else
        "partial"
    )
    record_connector_native_view_fn(
        graph,
        source_surface_id=source_surface_id,
        service="gmail",
        family="conversation",
        state=native_state,
        data=native_data or {"threads": [], "total_count": 0},
        refs=[run.id],
        service_extensions={
            "gmail": {
                "messages_imported": int(data.get("messages_imported") or 0),
                "thread_materialization": "ready" if native_data is not None else "pending",
                "thread_labels": {
                    obj.id: list(obj.data.get("labels") or [])
                    for obj in _objects(reader, "conversation_thread")
                    if obj.data.get("source_surface_id") == source_surface_id
                },
            }
        },
        error=data.get("error"),
        reader=reader,
    )


def gmail_run_id_for_object(reader, object_type: str, data: dict[str, Any]) -> Optional[str]:
    if object_type == "gmail_sync_run":
        return str(data.get("_object_id") or "") or None
    if object_type == "activity_evidence":
        return str((data.get("normalized_metadata") or {}).get("connector_run_id") or "") or None
    evidence_id = str(data.get("evidence_id") or "")
    if evidence_id:
        evidence = next((obj for obj in _objects(reader, "activity_evidence") if obj.id == evidence_id), None)
        if evidence is not None:
            return str((evidence.data.get("normalized_metadata") or {}).get("connector_run_id") or "") or None
    if object_type == "ingestion_failure":
        return str((data.get("metadata") or {}).get("run_id") or "") or None
    return None


def ensure_gmail_control_plane_fn(graph) -> int:
    """Backfill neutral adapters for stores created before connector_control.

    This migration remains Gmail-owned: BabyAGI calls one service helper and
    never interprets a Gmail field.
    """
    event_refs: dict[str, list[str]] = {}
    for event in graph.events:
        object_id = ""
        if event.type == "object.created":
            object_id = str((((event.payload or {}).get("object") or {}).get("id") or ""))
        elif event.type == "patch.applied":
            object_id = str((event.payload or {}).get("target") or "")
        if object_id:
            event_refs.setdefault(object_id, []).append(event.id)
    runs = list(graph.objects(type="gmail_sync_run"))
    for run in runs:
        refs = event_refs.get(run.id) or []
        adapt_gmail_run_fn(
            graph,
            run,
            source_event_id=refs[-1] if refs else None,
            attempt=not bool(
                next(
                    (
                        obj for obj in graph.objects(type="connector_run_observation")
                        if obj.data.get("domain_run_id") == run.id
                    ),
                    None,
                )
            ),
            reader=graph,
        )
    return len(runs)


__all__ = [
    "adapt_gmail_run_fn",
    "gmail_learning_settled",
    "gmail_run_id_for_object",
    "ensure_gmail_control_plane_fn",
]
