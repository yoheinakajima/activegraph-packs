"""activegraph.packs.communication — Communication Pack v0.1.

Channel-neutral communication semantic layer for all ActiveGraph packs.

Provides the shared primitive layer that all channel adapters (Chat, Email, SMS, Voice)
translate into. Domain packs (VC, Research) see comm_message objects regardless of
whether the request came from chat, email, or any other channel.

Object types:
  comm_thread             — Conversation thread (channel + subject + participants)
  comm_message            — Channel-neutral message (inbound or outbound)
  comm_intent             — Classified intent of a message (query/request/reply/etc.)
  comm_response_candidate — Proposed response pending approval and dispatch
  comm_participant        — A participant in a thread with a role

Behaviors:
  intent_detector      — comm_message.created → CommIntent (heuristic classification)
  thread_tracker       — comm_message.created → creates/updates CommThread
  response_dispatcher  — comm_response_candidate.created (status=approved) → marks sent

Behavior map:
  comm_message.created [inbound]
    → intent_detector  → comm_intent + intent_of relation
    → thread_tracker   → comm_thread (created/updated) + thread_contains + comm_participant

  comm_response_candidate.created [status=approved]
    → response_dispatcher → dispatched_to relation, status patched to "sent"

Composes with:
  - Core Pack (Source → drives principal_resolver in Identity Pack)
  - Identity Pack (principal_resolver fires on source.created from channel adapters)
  - Agent Profile Pack (ProfileContextView consumed by LLM responder behaviors)
  - Memory Gateway Pack (memory retrieval in LLM responder views)

Usage:
    from activegraph import Runtime, Graph
    from packs.core import pack as core_pack
    from packs.communication import pack as comm_pack, CommunicationSettings
    from packs.communication.tools import create_comm_message_fn

    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    rt.load_pack(comm_pack, settings=CommunicationSettings())

    msg = create_comm_message_fn(graph, channel="chat", content="What's the status?",
                                  sender_ref="alice@example.com", direction="inbound")
    rt.run_until_idle()
    # CommIntent + CommThread + CommParticipant are now in the graph

Entry point: registered as 'communication' in [project.entry-points."activegraph.packs"]
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import CommunicationSettings
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# requires=["core"], composes_with=["identity_auth", "agent_profile", "memory_gateway"]
pack = Pack(
    name="communication",
    version="0.2.0",
    description=(
        "Channel-neutral communication semantic layer. "
        "Provides CommThread, CommMessage, CommIntent, CommResponseCandidate, CommParticipant. "
        "intent_detector classifies message intent. thread_tracker maintains thread state. "
        "response_dispatcher routes approved responses to channel adapters."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=load_prompts_from_dir(_PROMPTS_DIR) if _PROMPTS_DIR.exists() else (),
    settings_schema=CommunicationSettings,
)

__all__ = ["pack", "CommunicationSettings"]
