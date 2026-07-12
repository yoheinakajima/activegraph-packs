"""Idempotent connector adapters and neutral control-plane queries."""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from activegraph.packs import tool

from .contracts import validate_native_data
from .object_types import (
    ConnectorLearningDelta,
    ConnectorNativeView,
    ConnectorRunObservation,
    ConnectorSurfaceBinding,
)
from .settings import ConnectorControlSettings


CONTROL_CONTRACT_VERSION = "connector_control@0.1.0"
TERMINAL_STATES = frozenset({"succeeded", "partial", "failed"})
SUCCESS_STATES = frozenset({"succeeded", "partial"})


def stable_connector_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()}"


def _by_field(graph, object_type: str, field: str, value: str):
    matches = [obj for obj in graph.objects(type=object_type) if obj.data.get(field) == value]
    if len(matches) > 1:
        raise RuntimeError(f"multiple {object_type} objects claim {field}={value!r}")
    return matches[0] if matches else None


def _append(values: list[str], value: Optional[str]) -> list[str]:
    out = list(values)
    if value and value not in out:
        out.append(value)
    return out


def _upsert(graph, object_type: str, identity_field: str, payload: dict[str, Any], immutable: tuple[str, ...], *, reader=None):
    existing = _by_field(reader or graph, object_type, identity_field, str(payload[identity_field]))
    if existing is None:
        return graph.add_object(object_type, payload), True
    for field in immutable:
        if existing.data.get(field) != payload.get(field):
            raise ValueError(
                f"{object_type} {payload[identity_field]!r} cannot change {field}"
            )
    updates = {key: value for key, value in payload.items() if existing.data.get(key) != value}
    if updates:
        if hasattr(graph, "objects"):
            graph.patch_object(
                existing.id,
                updates,
                rationale=f"refresh {object_type} adapter output",
            )
        else:
            # BehaviorGraph supplies actor/causality/rationale from the
            # behavior execution and intentionally accepts only target+data.
            graph.patch_object(existing.id, updates)
        return graph.get_object(existing.id), False
    return existing, False


def record_connector_binding_fn(
    graph,
    *,
    source_surface_id: str,
    service: str,
    account_ref: str,
    family: str,
    active_route: str,
    domain_run_type: str,
    routes: Optional[list[str]] = None,
    native_shape_version: int = 1,
    maintenance_mode: str = "none",
    manual_refresh_available: bool = False,
    status: str = "active",
    metadata: Optional[dict[str, Any]] = None,
    reader=None,
) -> dict[str, Any]:
    identity = stable_connector_id(
        "connector_binding", source_surface_id, service, account_ref, family
    )
    existing = _by_field(
        reader or graph, "connector_surface_binding", "binding_identity", identity
    )
    prior_routes = list(existing.data.get("routes") or []) if existing else []
    all_routes = list(dict.fromkeys([*prior_routes, *(routes or []), active_route]))
    payload = ConnectorSurfaceBinding(
        binding_identity=identity,
        source_surface_id=source_surface_id,
        service=service,
        account_ref=account_ref,
        family=family,
        routes=all_routes,
        active_route=active_route,
        domain_run_type=domain_run_type,
        native_shape_version=native_shape_version,
        maintenance_mode=maintenance_mode,
        manual_refresh_available=manual_refresh_available,
        status=status,
        metadata=dict(metadata or {}),
    ).model_dump()
    obj, created = _upsert(
        graph,
        "connector_surface_binding",
        "binding_identity",
        payload,
        ("source_surface_id", "service", "account_ref", "family"),
        reader=reader,
    )
    return {"ok": True, "created": created, "binding": obj}


def record_connector_run_observation_fn(
    graph,
    *,
    domain_run_id: str,
    source_surface_id: str,
    service: str,
    account_ref: str,
    family: str,
    route: str,
    state: str,
    phase: str,
    mode: str,
    source_event_id: Optional[str] = None,
    attempt: bool = False,
    health: Optional[str] = None,
    bounds: Optional[dict[str, Any]] = None,
    counts: Optional[dict[str, int]] = None,
    cursor: Optional[dict[str, Any]] = None,
    maintenance_mode: str = "none",
    manual_refresh_available: bool = False,
    next_sync_available: bool = False,
    error_code: Optional[str] = None,
    error: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    reader=None,
) -> dict[str, Any]:
    identity = stable_connector_id("connector_run", domain_run_id)
    existing = _by_field(
        reader or graph, "connector_run_observation", "observation_identity", identity
    )
    prior = dict(existing.data) if existing is not None else {}
    derived_health = health or {
        "queued": "connected",
        "running": "working",
        "succeeded": "current",
        "partial": "partial",
        "failed": "failed",
    }[state]
    updates = _append(list(prior.get("update_event_ids") or []), source_event_id)
    attempts = _append(list(prior.get("attempt_event_ids") or []), source_event_id if attempt else None)
    terminals = _append(list(prior.get("terminal_event_ids") or []), source_event_id if state in TERMINAL_STATES else None)
    successes = _append(list(prior.get("success_event_ids") or []), source_event_id if state in SUCCESS_STATES else None)
    payload = ConnectorRunObservation(
        observation_identity=identity,
        domain_run_id=domain_run_id,
        source_surface_id=source_surface_id,
        service=service,
        account_ref=account_ref,
        family=family,
        route=route,
        state=state,
        health=derived_health,
        phase=phase,
        mode=mode,
        bounds=dict(bounds or {}),
        counts={key: int(value) for key, value in (counts or {}).items()},
        cursor=dict(cursor or {}),
        attempt_event_ids=attempts,
        update_event_ids=updates,
        terminal_event_ids=terminals,
        success_event_ids=successes,
        maintenance_mode=maintenance_mode,
        manual_refresh_available=manual_refresh_available,
        next_sync_available=next_sync_available,
        error_code=error_code,
        error=(str(error)[:500] if error else None),
        metadata=dict(metadata or {}),
    ).model_dump()
    obj, created = _upsert(
        graph,
        "connector_run_observation",
        "observation_identity",
        payload,
        ("domain_run_id", "source_surface_id", "service", "account_ref", "family"),
        reader=reader,
    )
    return {"ok": True, "created": created, "observation": obj}


def record_connector_learning_delta_fn(
    graph,
    *,
    domain_run_id: str,
    source_surface_id: str,
    service: str,
    family: str,
    status: str = "collecting",
    evidence: Optional[dict[str, int]] = None,
    annotation_coverage: Optional[dict[str, int]] = None,
    resolutions: Optional[dict[str, int]] = None,
    candidates: Optional[dict[str, dict[str, int]]] = None,
    failures: int = 0,
    exceptions: Optional[list[dict[str, Any]]] = None,
    cost: Optional[dict[str, Any]] = None,
    refs: Optional[list[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
    settings: Optional[ConnectorControlSettings] = None,
    reader=None,
) -> dict[str, Any]:
    configured = settings or ConnectorControlSettings()
    identity = stable_connector_id("connector_delta", domain_run_id)
    payload = ConnectorLearningDelta(
        delta_identity=identity,
        domain_run_id=domain_run_id,
        source_surface_id=source_surface_id,
        service=service,
        family=family,
        status=status,
        evidence={key: int(value) for key, value in (evidence or {}).items()},
        annotation_coverage={key: int(value) for key, value in (annotation_coverage or {}).items()},
        resolutions={key: int(value) for key, value in (resolutions or {}).items()},
        candidates={
            key: {outcome: int(value) for outcome, value in outcomes.items()}
            for key, outcomes in (candidates or {}).items()
        },
        failures=failures,
        exceptions=list(exceptions or [])[: configured.max_exceptions_per_delta],
        cost=dict(cost or {}),
        refs=list(dict.fromkeys(refs or []))[: configured.max_refs_per_delta],
        metadata=dict(metadata or {}),
    ).model_dump()
    obj, created = _upsert(
        graph,
        "connector_learning_delta",
        "delta_identity",
        payload,
        ("domain_run_id", "source_surface_id", "service", "family"),
        reader=reader,
    )
    return {"ok": True, "created": created, "delta": obj}


def record_connector_native_view_fn(
    graph,
    *,
    source_surface_id: str,
    service: str,
    family: str,
    state: str,
    data: Optional[dict[str, Any]] = None,
    shape_version: int = 1,
    refs: Optional[list[str]] = None,
    service_extensions: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
    reader=None,
) -> dict[str, Any]:
    identity = stable_connector_id("connector_native_view", source_surface_id, family)
    canonical_data = validate_native_data(family, dict(data or {}))
    payload = ConnectorNativeView(
        view_identity=identity,
        source_surface_id=source_surface_id,
        service=service,
        family=family,
        shape_version=shape_version,
        state=state,
        data=canonical_data,
        refs=list(dict.fromkeys(refs or [])),
        service_extensions=dict(service_extensions or {}),
        error=(str(error)[:500] if error else None),
    ).model_dump()
    obj, created = _upsert(
        graph,
        "connector_native_view",
        "view_identity",
        payload,
        ("source_surface_id", "service", "family", "shape_version"),
        reader=reader,
    )
    return {"ok": True, "created": created, "view": obj}


def _event_index(graph) -> dict[str, dict[str, Any]]:
    return {
        event.id: {"event_id": event.id, "timestamp": event.timestamp}
        for event in graph.events
    }


def project_connector_control_plane_fn(graph) -> dict[str, Any]:
    event_index = _event_index(graph)
    bindings = sorted(
        (dict(obj.data) | {"binding_object_id": obj.id}
         for obj in graph.objects(type="connector_surface_binding")),
        key=lambda row: (row["service"], row["account_ref"], row["source_surface_id"]),
    )
    runs = []
    for obj in graph.objects(type="connector_run_observation"):
        data = dict(obj.data)
        attempts = list(data.get("attempt_event_ids") or [])
        successes = list(data.get("success_event_ids") or [])
        updates = list(data.get("update_event_ids") or [])
        runs.append({
            **data,
            "observation_object_id": obj.id,
            "last_attempt": event_index.get(attempts[-1]) if attempts else None,
            "last_success": event_index.get(successes[-1]) if successes else None,
            "last_update": event_index.get(updates[-1]) if updates else None,
            "current_work_ref": data.get("domain_run_id"),
        })
    runs.sort(key=lambda row: (
        str((row.get("last_update") or {}).get("event_id") or ""),
        row["observation_identity"],
    ))
    views = sorted(
        (dict(obj.data) | {"view_object_id": obj.id}
         for obj in graph.objects(type="connector_native_view")),
        key=lambda row: (row["source_surface_id"], row["family"]),
    )
    return {
        "contract_version": CONTROL_CONTRACT_VERSION,
        "bindings": bindings,
        "runs": runs,
        "native_views": views,
    }


def project_connector_learning_deltas_fn(graph) -> dict[str, Any]:
    rows = sorted(
        (dict(obj.data) | {"delta_object_id": obj.id}
         for obj in graph.objects(type="connector_learning_delta")),
        key=lambda row: (row["service"], row["source_surface_id"], row["domain_run_id"]),
    )
    return {"contract_version": CONTROL_CONTRACT_VERSION, "deltas": rows}


@tool(name="record_connector_binding", description="Bind one service surface to a connector family.")
def record_connector_binding(graph, source_surface_id: str = "", service: str = "", account_ref: str = "", family: str = "conversation", active_route: str = "native", domain_run_type: str = "connector_run", **kwargs):
    return record_connector_binding_fn(graph, source_surface_id=source_surface_id, service=service, account_ref=account_ref, family=family, active_route=active_route, domain_run_type=domain_run_type, **kwargs)


@tool(name="record_connector_run_observation", description="Adapt an authoritative domain run into neutral connector state.")
def record_connector_run_observation(graph, domain_run_id: str = "", source_surface_id: str = "", service: str = "", account_ref: str = "", family: str = "conversation", route: str = "native", state: str = "queued", phase: str = "queued", mode: str = "manual", **kwargs):
    return record_connector_run_observation_fn(graph, domain_run_id=domain_run_id, source_surface_id=source_surface_id, service=service, account_ref=account_ref, family=family, route=route, state=state, phase=phase, mode=mode, **kwargs)


@tool(name="record_connector_learning_delta", description="Record run-scoped learning counts and provenance.")
def record_connector_learning_delta(graph, domain_run_id: str = "", source_surface_id: str = "", service: str = "", family: str = "conversation", **kwargs):
    return record_connector_learning_delta_fn(graph, domain_run_id=domain_run_id, source_surface_id=source_surface_id, service=service, family=family, **kwargs)


@tool(name="record_connector_native_view", description="Record a validated family-native connector view.")
def record_connector_native_view(graph, source_surface_id: str = "", service: str = "", family: str = "conversation", state: str = "empty", **kwargs):
    return record_connector_native_view_fn(graph, source_surface_id=source_surface_id, service=service, family=family, state=state, **kwargs)


@tool(name="project_connector_control_plane", description="Project neutral connector status and native views.")
def project_connector_control_plane(graph, _ctx=None):
    return project_connector_control_plane_fn(graph)


@tool(name="project_connector_learning_deltas", description="Project run-scoped connector learning deltas.")
def project_connector_learning_deltas(graph, _ctx=None):
    return project_connector_learning_deltas_fn(graph)


TOOLS = [
    record_connector_binding,
    record_connector_run_observation,
    record_connector_learning_delta,
    record_connector_native_view,
    project_connector_control_plane,
    project_connector_learning_deltas,
]

__all__ = [
    "CONTROL_CONTRACT_VERSION",
    "TOOLS",
    "stable_connector_id",
    "record_connector_binding_fn",
    "record_connector_run_observation_fn",
    "record_connector_learning_delta_fn",
    "record_connector_native_view_fn",
    "project_connector_control_plane_fn",
    "project_connector_learning_deltas_fn",
]
