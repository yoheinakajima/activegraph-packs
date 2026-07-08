"""activegraph.packs.core — Core Pack v0.1.

The universal primitive layer for all ActiveGraph packs.

Provides the minimum shared substrate that every domain pack can build on:

  source          — Something received or observed (chat, email, file, tool result)
  observation     — A source-grounded noticed thing (weaker than a claim)
  task            — A minimal unit of work (deliberately underpowered)
  action          — A proposed or executed operation
  artifact        — A durable output (memo, draft, report)
  memory_candidate — Something that might be worth remembering
  evaluation      — A judgment about any of the above

Design invariants:
  - Core stays small. Do NOT add person, company, claim, evidence, or document.
  - Observation-first. Observations record what was noticed, not what is true.
  - Memory is candidate-first. Memory Gateway handles acceptance.
  - Actions flow through Tool Gateway before execution.
  - Behaviors are deterministic in v0.1 (no LLM required).

Usage:
    from activegraph import Runtime, Graph
    from packs.core import pack, CoreSettings

    rt = Runtime(Graph())
    rt.load_pack(pack, settings=CoreSettings())
    rt.run_goal("Process: hello world")

Entry point: registered as 'core' in [project.entry-points."activegraph.packs"]
so `load_by_name("core")` works after installation.
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import CoreSettings
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"

pack = Pack(
    name="core",
    version="0.1.2",
    description=(
        "Universal primitive substrate for all ActiveGraph packs. "
        "Provides 7 object types (source, observation, task, action, artifact, "
        "memory_candidate, evaluation) and 7 relation types. "
        "All behaviors are deterministic — no LLM or API key required."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=[],  # Core has no approval policies — those live in domain packs
    prompts=load_prompts_from_dir(_PROMPTS_DIR) if _PROMPTS_DIR.exists() else (),
    settings_schema=CoreSettings,
)

__all__ = ["pack", "CoreSettings"]
