"""Thin optional Composio route pack; enabled services own real profiles."""

from activegraph.packs import Pack
from activegraph.packs.manifest import CapabilityDecl

from .settings import ComposioSettings
from .tools import TOOLS

# requires=["tool_gateway"]
pack = Pack(
    name="composio",
    version="0.1.0",
    description=(
        "Optional thin Composio route: hosted Connect Links and per-service connection status. "
        "It never mirrors the Composio catalog or becomes a canonical service identity."
    ),
    object_types=(), relation_types=(), behaviors=(), tools=tuple(TOOLS), policies=(), prompts=(),
    capabilities=(
        CapabilityDecl(provider="composio", capability="connections.link", risk_class="high", credential_ref="", action_class="R4"),
        CapabilityDecl(provider="composio", capability="connections.status", risk_class="low", credential_ref="", action_class="R0"),
    ),
    settings_schema=ComposioSettings,
)

__all__ = ["pack", "ComposioSettings"]
