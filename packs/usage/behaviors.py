"""Materialize neutral usage facts from connection and normalizer events."""

from __future__ import annotations

from typing import Any

from activegraph.packs import behavior

from .settings import UsageSettings
from .tools import _coverage_from_times, _stable_id, validate_source_category


_VIEW = {
    "include_types": [
        "connection_surface",
        "settling_gate",
        "usage_evidence",
        "settlement_record",
    ],
    "recent_events": 10_000,
}


def _surface(view, surface_id: str):
    return next(
        (
            obj
            for obj in view.objects(type="connection_surface")
            if obj.data.get("surface_id") == surface_id
        ),
        None,
    )


def _gate(view, surface, settings: UsageSettings) -> dict[str, Any]:
    gate_id = surface.data.get("gate_id") or settings.default_gate_id
    gate_version = surface.data.get("gate_version") or settings.default_gate_version
    gate_obj = next(
        (
            obj
            for obj in view.objects(type="settling_gate")
            if obj.data.get("gate_id") == gate_id
            and obj.data.get("gate_version") == gate_version
        ),
        None,
    )
    if gate_obj is None:
        return {
            "gate_id": gate_id,
            "gate_version": gate_version,
            "min_unique_events": settings.min_unique_events,
            "min_coverage_days": settings.min_coverage_days,
            "allow_either": settings.allow_either,
        }
    return dict(gate_obj.data)


def _failure(graph, event, *, code: str, message: str, payload: dict[str, Any]):
    return graph.add_object(
        "usage_projection_failure",
        {
            "event_id": event.id,
            "source_surface_id": payload.get("source_surface_id")
            or payload.get("surface_id"),
            "evidence_identity": payload.get("evidence_identity"),
            "error_code": code,
            "message": message[:1000],
            "metadata": {},
        },
    )


def _current_evidence(view, surface_id: str) -> list[dict[str, Any]]:
    return [
        dict(obj.data)
        for obj in view.objects(type="usage_evidence")
        if obj.data.get("source_surface_id") == surface_id
        and obj.data.get("qualifying", False)
        and not obj.data.get("invalidated", False)
    ]


def _clock_times(view, surface_id: str) -> list[str]:
    return [
        event.payload.get("observed_at")
        for event in view.events(type="source.clock")
        if event.payload.get("surface_id") == surface_id
        and not event.payload.get("is_fixture", False)
        and event.payload.get("observed_at")
    ]


def _evaluate_surface(
    graph,
    ctx,
    event,
    surface,
    evidence_rows: list[dict[str, Any]],
    *,
    settings: UsageSettings,
) -> None:
    gate = _gate(ctx.view, surface, settings)
    unique_count = len({row["evidence_identity"] for row in evidence_rows})
    times = [row["provider_time"] for row in evidence_rows if row.get("provider_time")]
    if evidence_rows and surface.data.get("acquisition_mode") == "live":
        times.extend(_clock_times(ctx.view, surface.data["surface_id"]))
    coverage = _coverage_from_times(times)
    volume_passed = unique_count >= int(gate["min_unique_events"])
    coverage_passed = coverage["coverage_days"] >= int(gate["min_coverage_days"])
    passed = (
        volume_passed or coverage_passed
        if gate.get("allow_either", True)
        else volume_passed and coverage_passed
    )
    thresholds = [
        name
        for name, did_pass in (
            ("volume", volume_passed),
            ("coverage", coverage_passed),
        )
        if did_pass
    ]
    surface_id = surface.data["surface_id"]
    settlement_identity = _stable_id(
        "settlement", surface_id, gate["gate_id"], gate["gate_version"]
    )
    existing = next(
        (
            obj
            for obj in ctx.view.objects(type="settlement_record")
            if obj.data.get("settlement_identity") == settlement_identity
        ),
        None,
    )

    if (
        surface.data.get("status") in {"stale", "revoked", "failed"}
        and event.type != "source.connected"
    ):
        graph.patch_object(
            surface.id,
            {
                "unique_evidence_count": unique_count,
                "first_seen": coverage["earliest"],
                "last_seen": coverage["latest"],
            },
        )
        return

    if passed:
        if existing is None:
            settled_event = graph.emit(
                "source.settled",
                {
                    "surface_id": surface_id,
                    "source_surface_id": surface_id,
                    "category": surface.data["category"],
                    "gate_id": gate["gate_id"],
                    "gate_version": gate["gate_version"],
                    "unique_evidence_count": unique_count,
                    "coverage_days": coverage["coverage_days"],
                    "coverage_start": coverage["earliest"],
                    "coverage_end": coverage["latest"],
                    "passed_thresholds": thresholds,
                },
            )
            record = graph.add_object(
                "settlement_record",
                {
                    "settlement_identity": settlement_identity,
                    "source_surface_id": surface_id,
                    "source_category": surface.data["category"],
                    "gate_id": gate["gate_id"],
                    "gate_version": gate["gate_version"],
                    "unique_evidence_count": unique_count,
                    "coverage_days": coverage["coverage_days"],
                    "coverage_start": coverage["earliest"],
                    "coverage_end": coverage["latest"],
                    "passed_thresholds": thresholds,
                    "source_settled_event_id": settled_event.id,
                    "evaluated_at_event_id": event.id,
                },
            )
            graph.add_relation(record.id, surface.id, "settlement_for")
            settled_event_id = settled_event.id
        else:
            settled_event_id = existing.data["source_settled_event_id"]
        graph.patch_object(
            surface.id,
            {
                "status": "settled",
                "unique_evidence_count": unique_count,
                "first_seen": coverage["earliest"],
                "last_seen": coverage["latest"],
                "settled_by": thresholds,
                "settled_event_id": settled_event_id,
            },
        )
        return

    if existing is not None:
        status = "stale"
    elif evidence_rows:
        status = "settling"
        if surface.data.get("status") != "settling":
            graph.emit(
                "source.settling",
                {
                    "surface_id": surface_id,
                    "category": surface.data["category"],
                    "gate_id": gate["gate_id"],
                    "gate_version": gate["gate_version"],
                    "unique_evidence_count": unique_count,
                    "coverage_days": coverage["coverage_days"],
                },
            )
    else:
        status = "connected"
    graph.patch_object(
        surface.id,
        {
            "status": status,
            "unique_evidence_count": unique_count,
            "first_seen": coverage["earliest"],
            "last_seen": coverage["latest"],
            "settled_by": [],
        },
    )


@behavior(
    name="observe_normalized_evidence",
    on=["source.event_ingested"],
    view=_VIEW,
    creates=["usage_evidence", "settlement_record", "usage_projection_failure"],
)
def observe_normalized_evidence(event, graph, ctx, *, settings: UsageSettings):
    """Index one normalizer identity and evaluate its surface's current gate."""

    if not settings.enabled:
        return
    payload = dict(event.payload or {})
    surface_id = str(payload.get("source_surface_id") or "")
    surface = _surface(ctx.view, surface_id)
    if surface is None:
        _failure(
            graph,
            event,
            code="unknown_surface",
            message=f"evidence references unknown surface {surface_id!r}",
            payload=payload,
        )
        return
    try:
        category = validate_source_category(str(payload.get("source_category") or ""))
    except ValueError as exc:
        _failure(graph, event, code="unknown_category", message=str(exc), payload=payload)
        return
    required = {
        "evidence_identity": payload.get("evidence_identity"),
        "evidence_id": payload.get("evidence_id"),
        "revision_id": payload.get("revision_id"),
        "source_ref": payload.get("source_ref"),
        "importer_id": payload.get("importer_id"),
        "importer_version": payload.get("importer_version"),
    }
    missing = sorted(name for name, value in required.items() if not value)
    if missing:
        _failure(
            graph,
            event,
            code="incomplete_evidence_identity",
            message=f"qualifying evidence metadata missing: {', '.join(missing)}",
            payload=payload,
        )
        return
    if category != surface.data["category"] or payload.get("connection_path") != surface.data["path"]:
        _failure(
            graph,
            event,
            code="surface_metadata_mismatch",
            message="evidence category/path does not match its connection surface",
            payload=payload,
        )
        return

    identity = str(payload["evidence_identity"])
    existing = next(
        (
            obj
            for obj in ctx.view.objects(type="usage_evidence")
            if obj.data.get("evidence_identity") == identity
            and obj.data.get("source_surface_id") == surface_id
        ),
        None,
    )
    qualifying = bool(
        not payload.get("invalidated", False)
        and not payload.get("is_fixture", False)
        and not surface.data.get("is_fixture", False)
    )
    row = {
        "evidence_identity": identity,
        "evidence_id": payload["evidence_id"],
        "revision_id": payload["revision_id"],
        "revision_number": int(payload.get("revision_number", 1)),
        "source_surface_id": surface_id,
        "source_category": category,
        "connection_path": payload["connection_path"],
        "source_ref": payload["source_ref"],
        "provider_time": payload.get("provider_time"),
        "importer_id": payload["importer_id"],
        "importer_version": payload["importer_version"],
        "is_fixture": bool(payload.get("is_fixture", False)),
        "qualifying": qualifying,
        "invalidated": bool(payload.get("invalidated", False)),
        "first_ingested_event_id": (
            existing.data["first_ingested_event_id"] if existing else event.id
        ),
        "last_ingested_event_id": event.id,
        "revision_count": (
            int(existing.data.get("revision_count", 1))
            + (existing.data.get("revision_id") != payload["revision_id"])
            if existing
            else 1
        ),
    }
    if existing is None:
        index = graph.add_object("usage_evidence", row)
        graph.add_relation(index.id, surface.id, "evidence_on_surface")
    else:
        graph.patch_object(existing.id, row)

    current = [
        item
        for item in _current_evidence(ctx.view, surface_id)
        if item["evidence_identity"] != identity
    ]
    if qualifying:
        current.append(row)
    graph.patch_object(
        surface.id,
        {"events_seen": int(surface.data.get("events_seen", 0)) + 1},
    )
    _evaluate_surface(graph, ctx, event, surface, current, settings=settings)


@behavior(
    name="observe_surface_connection",
    on=["source.connected"],
    view=_VIEW,
    creates=["settlement_record"],
)
def observe_surface_connection(event, graph, ctx, *, settings: UsageSettings):
    """Re-evaluate existing evidence on reconnect without duplicating settlement."""

    surface_id = str(event.payload.get("surface_id") or "")
    surface = _surface(ctx.view, surface_id)
    if surface is None:
        return
    _evaluate_surface(
        graph,
        ctx,
        event,
        surface,
        _current_evidence(ctx.view, surface_id),
        settings=settings,
    )


@behavior(
    name="observe_cursor_progress",
    on=["source.cursor_advanced"],
    view=_VIEW,
    creates=["usage_projection_failure"],
)
def observe_cursor_progress(event, graph, ctx, *, settings: UsageSettings):
    """Project provider-stable cursor facts without implementing pagination."""

    payload = dict(event.payload or {})
    surface_id = str(payload.get("source_surface_id") or payload.get("surface_id") or "")
    surface = _surface(ctx.view, surface_id)
    if surface is None:
        _failure(
            graph,
            event,
            code="unknown_cursor_surface",
            message=f"cursor references unknown surface {surface_id!r}",
            payload=payload,
        )
        return
    graph.patch_object(
        surface.id,
        {
            "cursor_state": {
                "oldest_ingested_ref": payload.get("oldest_ingested_ref"),
                "newest_ingested_ref": payload.get("newest_ingested_ref"),
                "cursor_version": int(payload.get("cursor_version", 1)),
            }
        },
    )


@behavior(
    name="observe_source_clock",
    on=["source.clock"],
    view=_VIEW,
    creates=["settlement_record"],
)
def observe_source_clock(event, graph, ctx, *, settings: UsageSettings):
    """Re-evaluate a live surface using only its logged clock facts."""

    surface_id = str(event.payload.get("surface_id") or "")
    surface = _surface(ctx.view, surface_id)
    if surface is None or surface.data.get("acquisition_mode") != "live":
        return
    _evaluate_surface(
        graph,
        ctx,
        event,
        surface,
        _current_evidence(ctx.view, surface_id),
        settings=settings,
    )


@behavior(
    name="observe_evidence_invalidation",
    on=["source.evidence_invalidated", "source.event_invalidated"],
    view=_VIEW,
    creates=[],
)
def observe_evidence_invalidation(event, graph, ctx, *, settings: UsageSettings):
    """Remove one logical identity from current qualification without erasing it."""

    surface_id = str(event.payload.get("source_surface_id") or event.payload.get("surface_id") or "")
    identity = str(event.payload.get("evidence_identity") or "")
    index = next(
        (
            obj
            for obj in ctx.view.objects(type="usage_evidence")
            if obj.data.get("source_surface_id") == surface_id
            and obj.data.get("evidence_identity") == identity
        ),
        None,
    )
    surface = _surface(ctx.view, surface_id)
    if index is None or surface is None:
        return
    graph.patch_object(index.id, {"invalidated": True, "qualifying": False})
    current = [
        row
        for row in _current_evidence(ctx.view, surface_id)
        if row["evidence_identity"] != identity
    ]
    _evaluate_surface(graph, ctx, event, surface, current, settings=settings)


BEHAVIORS = [
    observe_normalized_evidence,
    observe_surface_connection,
    observe_cursor_progress,
    observe_source_clock,
    observe_evidence_invalidation,
]


__all__ = ["BEHAVIORS"]
