"""Semantic Extraction Pack — the shared annotation layer (ADR 0026).

Extract once into typed, source-anchored annotations; let every consumer
project from them. This pack owns the annotation contract (one provenance
envelope over the standard facets), the deterministic zero-key extractor,
the cache identity, first-class coverage, the ``extraction_profile``
config artifact (D042), and the first candidate projectors (profile,
memory). It emits annotations, never domain candidates — projectors are
separate, per-domain policy, and promotion gates are unchanged.

The existing activity_normalizer extraction path is untouched: migrating
it onto this layer follows ADR 0026's ordering in a later workstream.
"""

from __future__ import annotations

from activegraph.packs import Pack

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import SemanticExtractionSettings
from .tools import TOOLS


# requires=["activity_normalizer"]
# integrates_with=["core", "memory_gateway"]
pack = Pack(
    name="semantic_extraction",
    version="0.1.0",
    description=(
        "Shared annotation layer: typed source-anchored annotations under "
        "one provenance envelope, cache-identified deterministic extraction, "
        "first-class coverage, and per-domain candidate projectors."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=(),
    settings_schema=SemanticExtractionSettings,
)

__all__ = ["pack", "SemanticExtractionSettings"]
