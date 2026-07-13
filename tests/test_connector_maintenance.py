from activegraph import Graph, Runtime

from packs.connector_control import pack as connector_pack
from packs.connector_control.maintenance import (
    register_connector_maintenance_handler,
    request_connector_refresh_fn,
    unregister_connector_maintenance_handler,
)


def test_neutral_manual_refresh_dispatches_without_service_logic():
    graph = Graph(); runtime = Runtime(graph); runtime.load_pack(connector_pack)
    graph.add_object("connector_surface_binding", {
        "binding_identity": "binding", "source_surface_id": "calendar:one",
        "service": "fixture_calendar", "account_ref": "owner", "family": "schedule",
        "routes": ["native"], "active_route": "native", "domain_run_type": "fixture_run",
        "native_shape_version": 1, "maintenance_mode": "manual",
        "manual_refresh_available": True, "status": "active", "metadata": {},
    })
    register_connector_maintenance_handler(
        "fixture_calendar", lambda _graph, _binding, _request: {"run_id": "fixture-run-2"}
    )
    try:
        result = request_connector_refresh_fn(graph, "calendar:one")
    finally:
        unregister_connector_maintenance_handler("fixture_calendar")
    assert result["domain_run_id"] == "fixture-run-2"
    [request] = graph.objects(type="connector_maintenance_request")
    assert request.data["status"] == "accepted"
    assert request.data["service"] == "fixture_calendar"


def test_refresh_fails_closed_for_revoked_binding():
    graph = Graph(); runtime = Runtime(graph); runtime.load_pack(connector_pack)
    graph.add_object("connector_surface_binding", {
        "binding_identity": "binding", "source_surface_id": "mail:one",
        "service": "gmail", "account_ref": "owner", "family": "conversation",
        "routes": ["composio"], "active_route": "composio", "domain_run_type": "gmail_sync_run",
        "native_shape_version": 1, "maintenance_mode": "manual",
        "manual_refresh_available": True, "status": "revoked", "metadata": {},
    })
    try:
        request_connector_refresh_fn(graph, "mail:one")
    except ValueError as exc:
        assert "reconnect" in str(exc)
    else:
        raise AssertionError("revoked connector refresh must fail closed")


def test_source_lifecycle_updates_binding_authority_state():
    from packs.usage import pack as usage_pack
    from packs.usage.tools import connect_surface_fn, set_surface_status_fn

    graph = Graph(); runtime = Runtime(graph)
    runtime.load_pack(usage_pack); runtime.load_pack(connector_pack)
    connect_surface_fn(graph, "mail:life", "communication", path="native", provider={"service": "gmail"})
    graph.add_object("connector_surface_binding", {
        "binding_identity": "binding-life", "source_surface_id": "mail:life",
        "service": "gmail", "account_ref": "owner", "family": "conversation",
        "routes": ["native"], "active_route": "native", "domain_run_type": "gmail_sync_run",
        "native_shape_version": 1, "maintenance_mode": "manual",
        "manual_refresh_available": True, "status": "active", "metadata": {},
    })
    set_surface_status_fn(graph, "mail:life", "revoked", reason="owner revoked")
    runtime.run_until_idle()
    [binding] = graph.objects(type="connector_surface_binding")
    assert binding.data["status"] == "revoked"
