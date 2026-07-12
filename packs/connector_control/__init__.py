"""Connector Control Pack — cross-family status, learning, and read shapes."""

from activegraph.packs import Pack

from .object_types import OBJECT_TYPES, RELATION_TYPES
from .behaviors import BEHAVIORS
from .settings import ConnectorControlSettings
from .tools import TOOLS


pack = Pack(
    name="connector_control",
    version="0.1.0",
    description=(
        "Neutral connector surface bindings, authoritative-run adapters, "
        "learning deltas, and validated family-native read shapes."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=(),
    settings_schema=ConnectorControlSettings,
)

__all__ = ["pack", "ConnectorControlSettings"]
