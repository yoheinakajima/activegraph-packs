"""activegraph.packs.agent_profile — Agent Profile Pack v0.1.

Owns the assistant's goals, personality, style, and standing instructions.
Provides behavior-scoped context (not a global system prompt blob).

Object types:
  agent_profile        — Name, mission, personality description
  goal                 — Standing goal with priority + status
  standing_instruction — Scoped instruction (channel + audience_role filters)
  personality_profile  — Tone, verbosity, formality settings
  owner_preference     — Named key/value preference (domain + channel scoped)
  profile_context_request — Triggers context assembly
  profile_context_view — Assembled context for a channel/role

Behaviors:
  profile_registry_recorder    — agent_profile.created → local registry index
  goal_registry_recorder       — goal.created → local registry index
  instruction_registry_recorder — standing_instruction.created → local registry
  personality_registry_recorder — personality_profile.created → local registry
  preference_registry_recorder — owner_preference.created → local registry
  profile_context_provider     — profile_context_request.created → assembles view

Context assembly pattern:
  1. Add AgentProfile, Goals, StandingInstructions, etc. to the graph
  2. Create a ProfileContextRequest with profile_id + channel + audience_role
  3. Call rt.run_until_idle()
  4. Read the ProfileContextView from the graph

Composes with:
  - Identity Pack (audience_role from Principal shapes context slice)
  - Core Pack (observations can reference profile context)

Usage:
    from activegraph import Runtime, Graph
    from packs.agent_profile import pack as ap_pack, AgentProfileSettings
    from packs.agent_profile.tools import register_profile_fn, request_profile_context_fn

    rt = Runtime(Graph())
    rt.load_pack(ap_pack, settings=AgentProfileSettings(owner_name="Alice"))

    profile = register_profile_fn(graph, name="Aria", mission="Help Alice build her company.")
    rt.run_until_idle()

    request_profile_context_fn(graph, profile_id=profile.id, channel="email", audience_role="owner")
    rt.run_until_idle()
    # ProfileContextView is now in the graph
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import AgentProfileSettings
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# requires=["core"], composes_with=["identity_auth"]
pack = Pack(
    name="agent_profile",
    version="0.1.1",
    description=(
        "Agent identity, goals, standing instructions, and owner preferences. "
        "Context is behavior-scoped (not a global blob) — filtered by channel and audience role. "
        "ProfileContextRequest → profile_context_provider → ProfileContextView."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=load_prompts_from_dir(_PROMPTS_DIR) if _PROMPTS_DIR.exists() else (),
    settings_schema=AgentProfileSettings,
)

__all__ = ["pack", "AgentProfileSettings"]
