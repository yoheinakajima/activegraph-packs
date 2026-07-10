"""activegraph.packs.chat — Chat Adapter Pack v0.3.

Translates interactive chat input into channel-neutral CommMessage objects
and delivers assistant responses back to the chat interface.

Object types:
  chat_input   — Raw input from a chat interface (entry point)
  chat_session — Persistent session grouping multiple turns
  chat_turn    — Single request-response exchange (user_message + assistant_message)

Behaviors:
  chat_ingester       — chat_input.created → Source + CommMessage + ChatSession + ChatTurn
  chat_llm_responder  — comm_message.created (channel=chat) → CommResponseCandidate
                        (optionally agentic: gateway proxy tools in the native
                        LLM tool loop — see build_pack / ChatSettings.tool_allow_list)
  chat_responder      — comm_response_candidate.created (channel=chat, approved) → ChatTurn updated

Behavior map:
  chat_input.created
    → chat_ingester
        creates: source(kind=chat_message), comm_message(channel=chat, inbound),
                 chat_session (or resumes), chat_turn
        relations: derived_from_source, session_contains_turn, turn_from_input

  comm_message.created [channel=chat, direction=inbound]
    → chat_llm_responder
        creates: comm_response_candidate(channel=chat, status=approved*)
        *auto_approve_responses=True by default
        relations: response_to

  comm_response_candidate.created [channel=chat, status=approved]
    → chat_responder
        patches: chat_turn.assistant_message = content
    → response_dispatcher (Communication Pack)
        patches: comm_response_candidate.status = "sent"

Composes with:
  - Core Pack (source creation)
  - Communication Pack (CommMessage, CommResponseCandidate, thread_tracker, intent_detector)
  - Identity Pack (source.sender_ref → principal_resolver creates Principal)
  - Agent Profile Pack (ProfileContextView in LLM context when include_profile=True)

Usage:
    from activegraph import Runtime, Graph
    from packs.core import pack as core_pack
    from packs.communication import pack as comm_pack
    from packs.chat import pack as chat_pack, ChatSettings
    from packs.chat.tools import submit_chat_input_fn

    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    rt.load_pack(comm_pack)
    rt.load_pack(chat_pack, settings=ChatSettings(llm_provider="mock"))

    submit_chat_input_fn(graph, user_ref="alice@example.com", content="Hello!")
    rt.run_until_idle()
    # ChatTurn.assistant_message is populated

Entry point: registered as 'chat' in [project.entry-points."activegraph.packs"]
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import ChatSettings
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def build_pack(*, llm_tools=None, max_tool_turns: int = 4) -> Pack:
    """Construct the Chat Pack, optionally agentic.

    *llm_tools* is a list of runtime ``Tool`` objects — gateway proxies from
    ``packs.tool_gateway.llm_tools.llm_tools_for`` — that the responder may
    call in the native LLM tool loop. A build-time argument (not a setting)
    because @llm_behavior binds its tool set when the behavior object is
    constructed; bundles translate ``ChatSettings.tool_allow_list`` into this
    call (see bundles/assistant.py). With no tools this returns a pack
    identical to the module-level ``pack``.
    """
    if llm_tools:
        from .behaviors import make_llm_responder

        responder = make_llm_responder(tools=llm_tools, max_tool_turns=max_tool_turns)
        behaviors = [
            responder if getattr(b, "name", "") == "chat_llm_responder" else b
            for b in BEHAVIORS
        ]
    else:
        behaviors = BEHAVIORS

    # requires=["core", "communication"],
    # composes_with=["identity_auth", "agent_profile", "tool_gateway"]
    return Pack(
        name="chat",
        version="0.5.0",
        description=(
            "Chat adapter pack. Translates chat input into CommMessage(channel=chat). "
            "chat_ingester maps raw {'role': 'user', 'content': '...'} input into "
            "CommMessage + ChatSession + ChatTurn. chat_llm_responder assembles context "
            "and produces CommResponseCandidate (optionally calling Tool Gateway "
            "proxy tools in the native LLM loop). chat_responder delivers the response."
        ),
        object_types=OBJECT_TYPES,
        relation_types=RELATION_TYPES,
        behaviors=behaviors,
        tools=TOOLS,
        policies=(),
        prompts=load_prompts_from_dir(_PROMPTS_DIR) if _PROMPTS_DIR.exists() else (),
        settings_schema=ChatSettings,
    )


pack = build_pack()

__all__ = ["pack", "build_pack", "ChatSettings"]
