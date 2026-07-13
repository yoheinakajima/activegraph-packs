"""Seed the versioned cross-family operational release policy."""

from activegraph.packs import behavior

from .operational import OPERATIONAL_POLICY_ID, operational_policy_payload
from .settings import ConnectorControlSettings


@behavior(
    name="seed_connector_operational_policy",
    on=["pack.loaded"],
    where={"name": "connector_control"},
    view={"include_types": ["connector_operational_policy"]},
    creates=["connector_operational_policy"],
)
def seed_connector_operational_policy(
    event, graph, ctx, *, settings: ConnectorControlSettings
):
    del event, settings
    if any(
        obj.data.get("policy_identity") == OPERATIONAL_POLICY_ID
        for obj in ctx.view.objects(type="connector_operational_policy")
    ):
        return
    graph.add_object("connector_operational_policy", operational_policy_payload())


@behavior(
    name="project_source_lifecycle_to_connector_binding",
    on=["source.lifecycle_changed"],
    view={"include_types": ["connector_surface_binding"]},
    creates=[],
)
def project_source_lifecycle_to_connector_binding(
    event, graph, ctx, *, settings: ConnectorControlSettings
):
    del settings
    payload = event.payload or {}
    surface_id = str(payload.get("surface_id") or "")
    status = str(payload.get("status") or "")
    binding_status = {
        "connected": "active", "stale": "stale",
        "failed": "stale", "revoked": "revoked",
    }.get(status)
    if not surface_id or binding_status is None:
        return
    for binding in ctx.view.objects(type="connector_surface_binding"):
        if binding.data.get("source_surface_id") == surface_id and binding.data.get("status") != binding_status:
            graph.patch_object(binding.id, {"status": binding_status})


BEHAVIORS = [seed_connector_operational_policy, project_source_lifecycle_to_connector_binding]

__all__ = ["BEHAVIORS"]
