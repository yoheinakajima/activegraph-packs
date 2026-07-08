"""Assistant Bundle — the base bundle for any interactive assistant.

Packs included:
  core             — universal primitives (source, observation, task, action, artifact, memory_candidate, evaluation)
  tool_gateway     — capability execution with policy checks and credential injection
  secrets          — credential reference management (secrets never enter model context)
  memory_gateway   — memory lifecycle: candidate → evaluation → storage → retrieval
  agent_profile    — agent goals, personality, standing instructions, preferences
  identity_auth    — principal resolution, role-based permission checking
  communication    — channel-neutral messaging (CommThread, CommMessage, CommIntent)
  chat             — chat adapter (chat_input → CommMessage → ChatTurn)
"""

from __future__ import annotations

from activegraph import Graph, Runtime

from packs.core import pack as core_pack, CoreSettings
from packs.tool_gateway import pack as tool_gateway_pack, ToolGatewaySettings
from packs.secrets import pack as secrets_pack, SecretsSettings
from packs.memory_gateway import pack as memory_gateway_pack, MemoryGatewaySettings
from packs.agent_profile import pack as agent_profile_pack, AgentProfileSettings
from packs.identity_auth import pack as identity_auth_pack, IdentitySettings
from packs.communication import pack as communication_pack, CommunicationSettings
from packs.chat import build_pack as build_chat_pack, pack as chat_pack, ChatSettings


ASSISTANT_BUNDLE = [
    core_pack,
    tool_gateway_pack,
    secrets_pack,
    memory_gateway_pack,
    agent_profile_pack,
    identity_auth_pack,
    communication_pack,
    chat_pack,
]

# Alias for backward compatibility
ASSISTANT_PACK_LIST = ASSISTANT_BUNDLE


def load_assistant_packs(
    rt: Runtime,
    *,
    core_settings: CoreSettings | None = None,
    tool_gateway_settings: ToolGatewaySettings | None = None,
    secrets_settings: SecretsSettings | None = None,
    memory_gateway_settings: MemoryGatewaySettings | None = None,
    agent_profile_settings: AgentProfileSettings | None = None,
    identity_settings: IdentitySettings | None = None,
    communication_settings: CommunicationSettings | None = None,
    chat_settings: ChatSettings | None = None,
) -> Runtime:
    """Register the Assistant Bundle packs onto an existing Runtime.

    Factored out of ``build_assistant`` so the same pack set can be
    re-registered onto a Runtime rebuilt via ``Runtime.load(...)`` when
    resuming from a persisted SQLite event log. ``load_pack`` is
    idempotent and does not re-add domain objects, so calling this on a
    resumed runtime restores behaviors for future events without
    duplicating replayed state.
    """
    rt.load_pack(core_pack, settings=core_settings or CoreSettings())
    rt.load_pack(tool_gateway_pack, settings=tool_gateway_settings or ToolGatewaySettings())
    rt.load_pack(secrets_pack, settings=secrets_settings or SecretsSettings())
    rt.load_pack(memory_gateway_pack, settings=memory_gateway_settings or MemoryGatewaySettings())
    rt.load_pack(agent_profile_pack, settings=agent_profile_settings or AgentProfileSettings())
    rt.load_pack(identity_auth_pack, settings=identity_settings or IdentitySettings())
    rt.load_pack(communication_pack, settings=communication_settings or CommunicationSettings())
    rt.load_pack(
        _chat_pack_for(rt.graph, chat_settings, tool_gateway_settings),
        settings=chat_settings or ChatSettings(),
    )
    return rt


def _chat_pack_for(
    graph,
    chat_settings: ChatSettings | None,
    tool_gateway_settings: ToolGatewaySettings | None,
):
    """Resolve which Chat Pack to load: plain, or agentic.

    ChatSettings.tool_allow_list names gateway capabilities; @llm_behavior
    binds its tool set at behavior construction, so the allow-list is
    translated HERE — where the graph already exists for the proxies to
    close over — into gateway proxy Tools and a rebuilt pack. Empty
    allow-list → the ordinary module-level chat pack, unchanged.
    """
    cs = chat_settings or ChatSettings()
    if not cs.tool_allow_list:
        return chat_pack

    from packs.tool_gateway.llm_tools import llm_tools_for

    tools = llm_tools_for(
        graph,
        cs.tool_allow_list,
        settings=tool_gateway_settings or ToolGatewaySettings(),
    )
    return build_chat_pack(llm_tools=tools, max_tool_turns=cs.max_tool_turns)


def seed_default_profile(
    rt: Runtime,
    *,
    agent_profile_settings: AgentProfileSettings | None = None,
) -> str | None:
    """Seed a single default AgentProfile when none exists yet.

    A zero-config assistant should still be able to answer "who are you?" /
    "what is your mission?". This creates ONE AgentProfile from the profile
    settings' defaults (name + mission + owner) so chat_profile_context has
    something to assemble, then runs the runtime so the recorder behaviors
    index it in the local registry.

    Idempotent by design:
      * Fresh runtime → no agent_profile objects → seed one.
      * Resumed runtime (Runtime.load replays prior events) → the profile is
        already present → skip. (Use rebuild_profile_registry on resume to
        repopulate the in-memory index; replay does not fire recorders.)

    Returns the profile id (existing or newly seeded), or None if seeding was
    skipped because agent_profile is not loaded.
    """
    settings = agent_profile_settings or AgentProfileSettings()
    # graph.objects() is safe here — we are outside any behavior (build time).
    try:
        existing = list(rt.graph.objects(type="agent_profile"))
    except Exception:
        return None  # agent_profile object type not registered → pack absent.
    if existing:
        return existing[0].id

    from packs.agent_profile.tools import register_profile_fn

    profile = register_profile_fn(
        rt.graph,
        name=settings.default_agent_name,
        mission=settings.default_mission,
        owner_name=settings.owner_name,
    )
    # Settle the creation so profile_registry_recorder indexes it.
    rt.run_until_idle()
    return profile.id


def build_assistant(
    *,
    core_settings: CoreSettings | None = None,
    tool_gateway_settings: ToolGatewaySettings | None = None,
    secrets_settings: SecretsSettings | None = None,
    memory_gateway_settings: MemoryGatewaySettings | None = None,
    agent_profile_settings: AgentProfileSettings | None = None,
    identity_settings: IdentitySettings | None = None,
    communication_settings: CommunicationSettings | None = None,
    chat_settings: ChatSettings | None = None,
    llm_provider=None,
    persist_to: str | None = None,
    seed_profile: bool = True,
) -> Runtime:
    """Create a Runtime with the Assistant Bundle loaded.

    This is the base bundle for any interactive assistant. It provides
    the full infrastructure stack: identity, memory, tool execution,
    communication, and chat.

    Args:
        core_settings: Override CoreSettings. Defaults to CoreSettings().
        tool_gateway_settings: Override ToolGatewaySettings.
        secrets_settings: Override SecretsSettings.
        memory_gateway_settings: Override MemoryGatewaySettings.
        agent_profile_settings: Override AgentProfileSettings.
        identity_settings: Override IdentitySettings.
        communication_settings: Override CommunicationSettings.
        chat_settings: Override ChatSettings.
        llm_provider: LLM provider for LLM-backed behaviors (optional in v0.1).
        persist_to: Optional path to a SQLite file. When provided, the
            runtime attaches a durable event store so all events are
            written to disk and the run can be resumed with
            ``Runtime.load(path)``. When ``None`` the graph is in-memory
            only (the historical default).

    Returns:
        A configured Runtime ready to run goals.
    """
    graph = Graph()
    kwargs = {}
    # chat_llm_responder is an @llm_behavior, so the runtime needs a provider.
    # Default to the chat MockChatProvider (no API key needed) — callers that
    # have a real provider key pass it in explicitly.
    if llm_provider is None:
        from packs.chat.llm import MockChatProvider
        llm_provider = MockChatProvider()
    kwargs["llm_provider"] = llm_provider
    if persist_to is not None:
        kwargs["persist_to"] = persist_to

    rt = Runtime(graph, **kwargs)

    load_assistant_packs(
        rt,
        core_settings=core_settings,
        tool_gateway_settings=tool_gateway_settings,
        secrets_settings=secrets_settings,
        memory_gateway_settings=memory_gateway_settings,
        agent_profile_settings=agent_profile_settings,
        identity_settings=identity_settings,
        communication_settings=communication_settings,
        chat_settings=chat_settings,
    )

    # Give a fresh assistant a self-description out of the box so chat can answer
    # "who are you?" with no setup. Opt out with seed_profile=False.
    if seed_profile:
        seed_default_profile(rt, agent_profile_settings=agent_profile_settings)

    return rt


if __name__ == "__main__":
    print("Building assistant bundle...")
    rt = build_assistant()
    print(f"Loaded {len(ASSISTANT_BUNDLE)} packs:")
    for p in ASSISTANT_BUNDLE:
        print(f"  - {p.name} v{p.version}")
