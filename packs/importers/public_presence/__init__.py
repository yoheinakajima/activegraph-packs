"""Public-presence bootstrap fetcher (ADR 0024 rung 1).

A gateway-routed, budgeted, R0 capability that fetches the owner's
shared public handles (GitHub profile, personal site, X profile page)
with the zero-key floor (stdlib fetch + stdlib HTML→text). Every fetch
is a recorded capability call; every result is replay-retained (artifact
mode) and injection-scanned before it lands as evidence on the
``public_presence`` surface. A keyed Firecrawl-grade fetcher sits behind
the same seam as config — suggested, never required.
"""

from __future__ import annotations

from activegraph.packs import Pack
from activegraph.packs.manifest import CapabilityDecl

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import PublicPresenceSettings
from .tools import TOOLS


# requires=["activity_normalizer", "tool_gateway"]
pack = Pack(
    name="public_presence",
    version="0.1.0",
    description=(
        "Budgeted, gateway-routed R0 fetching of the owner's public "
        "handles; results land as injection-scanned, replay-retained "
        "evidence on the public_presence surface."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=(),
    settings_schema=PublicPresenceSettings,
    capabilities=(
        CapabilityDecl(
            provider="public_presence",
            capability="fetch_page",
            risk_class="low",
            credential_ref="",
            action_class="R0",
        ),
    ),
)

__all__ = ["pack", "PublicPresenceSettings"]
