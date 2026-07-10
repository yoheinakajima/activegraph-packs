"""Local Files importer pack.

Loads a bounded directory snapshot into Activity Normalizer's acquired-item
contract. The importer is a format adapter only: identity, deduplication,
revisioning, evidence, and candidate extraction remain normalizer-owned.
"""

from __future__ import annotations

from activegraph.packs import Pack

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import LocalFilesSettings
from .tools import TOOLS

# requires=["activity_normalizer"]
pack = Pack(
    name="local_files",
    version="0.1.0",
    description=(
        "Bounded, deterministic directory snapshots for UTF-8 text, Markdown, "
        "and JSON, emitted through Activity Normalizer's acquired-item contract."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=(),
    settings_schema=LocalFilesSettings,
)

__all__ = ["pack", "LocalFilesSettings"]
