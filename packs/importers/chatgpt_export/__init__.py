"""Official ChatGPT export importer.

Parses ``conversations.json`` as a tree and emits strict Activity Normalizer
``acquired_item`` / ``acquired_content`` handoffs. Evidence identity,
deduplication, extraction, and promotion remain normalizer-owned.
"""

from __future__ import annotations

from activegraph.packs import Pack

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import ChatGPTExportSettings
from .tools import TOOLS


# requires=["activity_normalizer"]
pack = Pack(
    name="chatgpt_export",
    version="0.1.0",
    description=(
        "Bounded deterministic ingestion of official ChatGPT export ZIPs, "
        "including canonical conversation paths and abandoned branches."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=(),
    settings_schema=ChatGPTExportSettings,
)

__all__ = ["pack", "ChatGPTExportSettings"]
