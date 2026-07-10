"""Activity normalizer: acquired items to evidence and typed candidates.

# requires=["core"]
"""

from __future__ import annotations

from activegraph.packs import Pack

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import ActivityNormalizerSettings
from .tools import TOOLS


pack = Pack(
    name="activity_normalizer",
    version="0.2.0",
    description=(
        "Provider-neutral activity evidence identity, replay, revision, and "
        "deterministic candidate extraction."
    ),
    object_types=tuple(OBJECT_TYPES),
    relation_types=tuple(RELATION_TYPES),
    behaviors=tuple(BEHAVIORS),
    tools=tuple(TOOLS),
    policies=(),
    prompts=(),
    settings_schema=ActivityNormalizerSettings,
)


__all__ = ["pack", "ActivityNormalizerSettings"]
