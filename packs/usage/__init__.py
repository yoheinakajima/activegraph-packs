"""Neutral source connection, settlement, coverage, and interaction facts.

# requires=["activity_normalizer"]
"""

from __future__ import annotations

from activegraph.packs import Pack

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import UsageSettings
from .tools import TOOLS


pack = Pack(
    name="usage",
    version="0.1.0",
    description=(
        "Deterministic connection, settlement, coverage, interaction, and outcome observations."
    ),
    object_types=tuple(OBJECT_TYPES),
    relation_types=tuple(RELATION_TYPES),
    behaviors=tuple(BEHAVIORS),
    tools=tuple(TOOLS),
    policies=(),
    prompts=(),
    settings_schema=UsageSettings,
)


__all__ = ["pack", "UsageSettings"]
