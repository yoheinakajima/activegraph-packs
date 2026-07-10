"""Canonical outcomes and artifact-owned reliability effects."""

from __future__ import annotations

from activegraph.packs import Pack

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import EvalOutcomeSettings
from .tools import TOOLS


# requires=["core"]
# integrates_with=["usage", "skills", "memory_gateway", "tool_gateway"]
pack = Pack(
    name="eval_outcome",
    version="0.1.0",
    description=(
        "Canonical terminal and maintenance outcomes with traceable, "
        "recency-aware artifact reliability and reversible eligibility hooks."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=(),
    settings_schema=EvalOutcomeSettings,
)


__all__ = ["pack", "EvalOutcomeSettings"]
