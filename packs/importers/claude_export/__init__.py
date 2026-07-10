"""Official Claude export importer.

Parses ``conversations.json`` as a flat conversation list and emits strict
Activity Normalizer ``acquired_item`` / ``acquired_content`` handoffs.
Evidence identity, deduplication, extraction, and promotion remain
normalizer-owned.
"""

from __future__ import annotations

from activegraph.packs import Pack

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import ClaudeExportSettings
from .tools import TOOLS


# requires=["activity_normalizer"]
pack = Pack(
    name="claude_export",
    version="0.1.0",
    description=(
        "Bounded deterministic ingestion of official Claude data exports, "
        "accepting the export ZIP or a bare conversations.json snapshot."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=(),
    settings_schema=ClaudeExportSettings,
)

__all__ = ["pack", "ClaudeExportSettings"]
