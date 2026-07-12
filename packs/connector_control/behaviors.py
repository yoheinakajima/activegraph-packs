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


BEHAVIORS = [seed_connector_operational_policy]

__all__ = ["BEHAVIORS"]
