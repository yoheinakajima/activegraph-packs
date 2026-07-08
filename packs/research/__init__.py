"""activegraph.packs.research — Research Pack v0.1.

Paper/claim/method/idea discovery and hypothesis generation.

Object types:
  paper              — Research paper with abstract, authors, venue, keywords
  author             — Research author with affiliation
  venue              — Publication venue (journal, conference, workshop)
  method             — Research method or algorithm
  benchmark          — Benchmark task with SOTA tracking
  dataset            — Research dataset
  idea_atom          — Atomic research idea distilled from papers
  research_direction — Synthesized direction from multiple idea atoms
  experiment         — Proposed or running research experiment

Behaviors:
  paper_ingester                 — source.created (kind=research_paper) → Paper + Author + Venue
  claim_extractor                — paper.created → observations (claims)
  idea_atom_extractor            — paper.created → IdeaAtom objects
  hypothesis_generator           — idea_atom.created (high coherence) → ResearchDirection
  research_direction_synthesizer — idea_atom.created → merges into synthesized direction

Relation types: cites, authored_by, published_in, uses_method, reports_benchmark,
                uses_dataset, proposes_idea, composes_direction, tests_direction

Composes with: Core Pack (observations, tasks, artifacts), Entity Pack (author entities)
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import ResearchSettings
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# requires=["core"], composes_with=["entity", "memory_gateway"]
pack = Pack(
    name="research",
    version="0.1.1",
    description=(
        "Research paper ingestion, claim extraction, idea atom distillation, "
        "and hypothesis generation. All behaviors deterministic in v0.1 (mock LLM stubs). "
        "Provides 9 object types for scientific knowledge tracking."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=load_prompts_from_dir(_PROMPTS_DIR) if _PROMPTS_DIR.exists() else (),
    settings_schema=ResearchSettings,
)

__all__ = ["pack", "ResearchSettings"]
