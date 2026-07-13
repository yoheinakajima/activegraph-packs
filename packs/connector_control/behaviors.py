"""Seed the versioned cross-family operational release policy."""

from activegraph.packs import behavior

from .operational import (
    OPERATIONAL_POLICY_ID,
    SUPERSEDED_POLICY_IDS,
    operational_policy_payload,
)
from .plans import settle_plan_for_run_fn
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
    # Stores seeded before v2 carry the old policy version; supersede it so
    # exactly one policy stays active for ceilings and model budgets.
    for obj in ctx.view.objects(type="connector_operational_policy"):
        if (
            obj.data.get("policy_identity") in SUPERSEDED_POLICY_IDS
            and obj.data.get("status") == "active"
        ):
            graph.patch_object(obj.id, {"status": "superseded"})
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


@behavior(
    name="settle_ingestion_plan_on_terminal_run",
    on=["object.created", "patch.applied"],
    view={"include_types": ["connector_ingestion_plan", "connector_run_observation"]},
    creates=[],
)
def settle_ingestion_plan_on_terminal_run(
    event, graph, ctx, *, settings: ConnectorControlSettings
):
    """Fulfill or release the executing plan when its bound run turns terminal.

    Neutral by construction: any service that records run observations gets
    plan settlement without family-specific code (ADR 0039).
    """
    del settings
    payload = event.payload or {}
    if event.type == "object.created":
        wrapper = payload.get("object") or {}
        if wrapper.get("type") != "connector_run_observation":
            return
        data = wrapper.get("data") or {}
    else:
        target = str(payload.get("target") or "")
        obj = graph.get_object(target) if target else None
        if obj is None or obj.type != "connector_run_observation":
            return
        data = obj.data or {}
    state = str(data.get("state") or "")
    if state not in {"succeeded", "partial", "failed"}:
        return
    settle_plan_for_run_fn(
        graph,
        domain_run_id=str(data.get("domain_run_id") or ""),
        state=state,
        reader=ctx.view,
    )


BEHAVIORS = [
    seed_connector_operational_policy,
    project_source_lifecycle_to_connector_binding,
    settle_ingestion_plan_on_terminal_run,
]

__all__ = ["BEHAVIORS"]
