"""Attention Pack — semantic evidence and learned importance/trust vectors."""

from __future__ import annotations

from activegraph.packs import Pack

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import AttentionSettings
from .tools import TOOLS


pack = Pack(
    name="attention",
    version="0.2.0",
    description=(
        "Semantic engagement observations plus explainable, scoped importance "
        "and source-trust vectors learned from recorded outcomes."
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
