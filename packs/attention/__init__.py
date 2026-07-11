"""Attention Pack — semantic engagement evidence for learned vectors.

This pack deliberately does not own BabyAGI score, policy, authority, raw UI
telemetry, or a universal mutable importance field.
"""

from __future__ import annotations

from activegraph.packs import Pack

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import AttentionSettings
from .tools import TOOLS


pack = Pack(
    name="attention",
    version="0.1.0",
    description=(
        "Semantic engagement observations and bounded interaction batches, "
        "without choosing the future importance/trust vector representation."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=(),
    settings_schema=AttentionSettings,
)

__all__ = ["pack", "AttentionSettings"]
