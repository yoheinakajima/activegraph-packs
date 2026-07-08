"""activegraph.packs.entity — Entity Pack v0.1.

Canonical, deduped representation of real-world entities.

Object types:
  entity           — Person, organization, project, product, repo, or other
  entity_mention   — A reference to an entity found in a source
  merge_candidate  — Two entities with high similarity (pending dedup)
  merge_decision   — Accept, reject, or defer a merge candidate

Behaviors:
  entity_registry_recorder — entity.created → indexes in local registry
  entity_extractor         — source.created → creates entity_mention objects
  entity_resolver          — entity_mention.created → links to or creates entity
  merge_candidate_detector — entity.created → finds high-similarity pairs

Dedup flow:
  source.created
    → entity_extractor (heuristic extraction) → entity_mention
    → entity_resolver (registry lookup) → links mention to entity or creates new entity
    → merge_candidate_detector → creates merge_candidate if similarity ≥ threshold
  operator reviews merge_candidates → creates merge_decisions
  decide_merge_fn("accepted") → merged_into relation + status patch

Composes with:
  - Core Pack (entity_extractor fires on source.created)
  - Identity Pack (Principal.entity_id can point to an Entity)
  - VC/Research Packs (extend Entity with domain overlays)

Usage:
    from activegraph import Runtime, Graph
    from packs.core import pack as core_pack
    from packs.entity import pack as entity_pack, EntitySettings
    from packs.entity.behaviors import clear_entity_registry

    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    rt.load_pack(entity_pack, settings=EntitySettings())

    graph.add_object("source", {
        "kind": "email",
        "content": "Alice Chen from Northwind Robotics sent alice@northwind.ai",
    })
    rt.run_until_idle()
    # entity_mention + entity objects are now in the graph
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import EntitySettings
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# requires=["core"], composes_with=["identity_auth"]
pack = Pack(
    name="entity",
    version="0.1.2",
    description=(
        "Canonical entity extraction, resolution, and dedup for people, "
        "organizations, projects, products, and repos. "
        "Heuristic extraction → alias-based resolution → MergeCandidate dedup flow."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=load_prompts_from_dir(_PROMPTS_DIR) if _PROMPTS_DIR.exists() else (),
    settings_schema=EntitySettings,
)

__all__ = ["pack", "EntitySettings"]
