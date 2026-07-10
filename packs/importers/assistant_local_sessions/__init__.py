"""Assistant Local Sessions importer pack.

Imports local agent-session JSONL logs (Claude Code ``projects/`` and Codex
``sessions/`` layouts) into Activity Normalizer's strict acquired-item
contract, one record pair per user/assistant message line, inside a bounded
most-recent-files window.  The importer is a format adapter only: identity,
deduplication, revisioning, evidence, and candidate extraction remain
normalizer-owned.
"""

from __future__ import annotations

from activegraph.packs import Pack

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import AssistantLocalSessionsSettings
from .tools import TOOLS

# requires=["activity_normalizer"]
pack = Pack(
    name="assistant_local_sessions",
    version="0.1.0",
    description=(
        "Bounded, deterministic ingestion of local agent-session JSONL logs "
        "(Claude Code and Codex), windowed to the most recent session files "
        "and parsed defensively against unversioned format drift."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=(),
    settings_schema=AssistantLocalSessionsSettings,
)

__all__ = ["pack", "AssistantLocalSessionsSettings"]
