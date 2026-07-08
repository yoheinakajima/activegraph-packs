"""activegraph.packs.memory_gateway — Memory Gateway Pack v0.1.

Manages the full memory lifecycle:
  memory_candidate (Core) → evaluation → memory_item (stored) → retrieval

Key invariants:
  - Core Pack only produces memory_candidate objects
  - Memory Gateway decides what to keep (acceptance_threshold)
  - Memory is candidate-first: never write MemoryItems directly
  - Retrieval is graph-visible: every query creates a MemoryRetrieval object
  - Default backend: in-memory SQLite (no persistence across runs)

Object types: memory_item, memory_retrieval, memory_ranking
Behaviors:    candidate_evaluator, memory_writer, memory_ranker
Tools:        retrieve_memories
Backend:      SqliteMemoryBackend (packs/memory_gateway/backend.py)

Usage:
    from activegraph import Runtime, Graph
    from packs.core import pack as core_pack, CoreSettings
    from packs.memory_gateway import pack as mg_pack, MemoryGatewaySettings

    rt = Runtime(Graph())
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(mg_pack, settings=MemoryGatewaySettings(
        acceptance_threshold=0.6,
        max_items=500,
    ))
    # Now when Core creates memory_candidates, Memory Gateway evaluates them.

Entry point: registered as 'memory_gateway' in pyproject.toml
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import MemoryGatewaySettings
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# requires=["core"], integrates_with=["tool_gateway"]
pack = Pack(
    name="memory_gateway",
    version="0.2.0",
    description=(
        "Memory lifecycle manager. Evaluates memory_candidates from Core Pack, "
        "accepts high-confidence items into durable MemoryItems, and provides "
        "keyword-ranked retrieval. Default backend: in-memory SQLite."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=load_prompts_from_dir(_PROMPTS_DIR) if _PROMPTS_DIR.exists() else (),
    settings_schema=MemoryGatewaySettings,
)

__all__ = ["pack", "MemoryGatewaySettings"]
