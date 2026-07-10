"""Governed learned skills: immutable versions, usage, and lifecycle proof."""

from __future__ import annotations

from activegraph.packs import Pack

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import SkillsSettings
from .tools import TOOLS


# requires=["core"]
# integrates_with=["activity_normalizer", "tool_gateway", "eval_outcome"]
pack = Pack(
    name="skills",
    version="0.1.0",
    description=(
        "Versioned, provenance-backed learned behavior artifacts with exact-version "
        "usage, evidence-gated promotion, and reversible eligibility."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=(),
    settings_schema=SkillsSettings,
)


__all__ = ["pack", "SkillsSettings"]
