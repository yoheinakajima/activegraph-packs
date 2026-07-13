"""Service-neutral connector maintenance request registry."""

from __future__ import annotations

import hashlib
from typing import Any, Callable


MaintenanceHandler = Callable[[Any, Any, Any], dict[str, Any]]
_HANDLERS: dict[str, MaintenanceHandler] = {}


def register_connector_maintenance_handler(
    service: str, handler: MaintenanceHandler, *, replace: bool = True
) -> None:
    key = service.strip().lower()
    if not key:
        raise ValueError("service is required")
    if key in _HANDLERS and not replace:
        raise ValueError(f"maintenance handler already registered for {key!r}")
    _HANDLERS[key] = handler


def clear_connector_maintenance_handlers() -> None:
    _HANDLERS.clear()


def unregister_connector_maintenance_handler(service: str) -> None:
    _HANDLERS.pop(service.strip().lower(), None)


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "\x1f".join(str(part) for part in parts).encode()
    return f"{prefix}_{hashlib.sha256(raw).hexdigest()[:24]}"


def request_connector_refresh_fn(
    graph,
    source_surface_id: str,
    *,
    requested_by: str = "owner",
) -> dict[str, Any]:
    bindings = [
        obj for obj in graph.objects(type="connector_surface_binding")
        if obj.data.get("source_surface_id") == source_surface_id
    ]
    if len(bindings) != 1:
        raise ValueError(f"expected one connector binding for {source_surface_id!r}")
    binding = bindings[0]
    data = binding.data or {}
    if data.get("status") != "active":
        raise ValueError("connector is not active; reconnect before refreshing")
    if not data.get("manual_refresh_available"):
        raise ValueError("connector does not currently permit manual refresh")

    active = [
        obj for obj in graph.objects(type="connector_run_observation")
        if obj.data.get("source_surface_id") == source_surface_id
        and obj.data.get("state") in {"queued", "running"}
    ]
    if active:
        return {
            "ok": True, "created": False, "already_running": True,
            "domain_run_id": active[-1].data.get("domain_run_id"),
        }

    prior = [
        obj for obj in graph.objects(type="connector_maintenance_request")
        if obj.data.get("source_surface_id") == source_surface_id
    ]
    request = graph.add_object("connector_maintenance_request", {
        "request_identity": _stable_id("connector_refresh", source_surface_id, len(prior) + 1),
        "source_surface_id": source_surface_id,
        "service": str(data.get("service") or ""),
        "account_ref": str(data.get("account_ref") or ""),
        "kind": "manual_refresh", "requested_by": requested_by,
        "status": "proposed", "domain_run_id": None, "error": None,
        "metadata": {"binding_id": binding.id},
    })
    handler = _HANDLERS.get(str(data.get("service") or "").lower())
    if handler is None:
        graph.patch_object(request.id, {"status": "failed", "error": "maintenance_handler_unavailable"}, rationale="connector refresh has no registered service handler")
        return {"ok": False, "created": True, "request_id": request.id, "error": "maintenance_handler_unavailable"}
    try:
        result = handler(graph, binding, request)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]
        graph.patch_object(request.id, {"status": "failed", "error": error}, rationale="connector maintenance handler failed")
        return {"ok": False, "created": True, "request_id": request.id, "error": error}
    domain_run_id = str(result.get("run_id") or result.get("domain_run_id") or "") or None
    graph.patch_object(request.id, {"status": "accepted", "domain_run_id": domain_run_id}, rationale="connector maintenance work accepted")
    return {"ok": True, "created": True, "request_id": request.id, "domain_run_id": domain_run_id, "service_result": result}


__all__ = [
    "register_connector_maintenance_handler",
    "clear_connector_maintenance_handlers",
    "unregister_connector_maintenance_handler",
    "request_connector_refresh_fn",
]
