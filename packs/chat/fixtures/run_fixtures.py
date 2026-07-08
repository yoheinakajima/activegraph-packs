"""Chat Adapter Pack fixtures.

Tests:
  1. 3-turn conversation — full pipeline: chat_input → CommMessage → CommResponseCandidate
     → ChatTurn.assistant_message populated
  2. Session continuity — second set of turns resumes the same ChatSession
  3. Mock LLM responses — deterministic stubs, no API key required
  4. Cross-pack: Chat + Communication + Core all wired together
"""

from __future__ import annotations

import sys

from activegraph import Graph, Runtime

from packs.core import pack as core_pack, CoreSettings
from packs.communication import pack as comm_pack, CommunicationSettings
from packs.communication.behaviors import clear_thread_registry
from packs.chat import pack as chat_pack, ChatSettings
from packs.chat.behaviors import clear_session_registry
from packs.chat.llm import MockChatProvider
from packs.chat.tools import get_session_turns_fn, submit_chat_input_fn


def _make_runtime(auto_approve: bool = True) -> tuple:
    clear_thread_registry()
    clear_session_registry()

    g = Graph()
    # chat_llm_responder is an @llm_behavior — the runtime requires a provider.
    # MockChatProvider runs the pipeline end-to-end with no API key.
    rt = Runtime(g, llm_provider=MockChatProvider())
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(comm_pack, settings=CommunicationSettings())
    rt.load_pack(
        chat_pack,
        settings=ChatSettings(
            llm_provider="mock",
            auto_approve_responses=auto_approve,
        ),
    )
    return g, rt


def run_three_turn_conversation_fixture() -> dict:
    """Demonstrate a 3-turn conversation: each turn produces observations, tasks, and artifacts."""
    g, rt = _make_runtime()

    # ── Turn 1 ──────────────────────────────────────────────────────────────
    submit_chat_input_fn(g, user_ref="alice@example.com",
                         content="What is the status of the Northwind deal?")
    rt.run_until_idle()

    sessions_t1 = list(g.objects(type="chat_session"))
    turns_t1 = list(g.objects(type="chat_turn"))
    comm_msgs_t1 = list(g.objects(type="comm_message"))
    comm_threads_t1 = list(g.objects(type="comm_thread"))
    intents_t1 = list(g.objects(type="comm_intent"))
    candidates_t1 = list(g.objects(type="comm_response_candidate"))

    assert len(sessions_t1) == 1, f"Turn 1: expected 1 session, got {len(sessions_t1)}"
    assert len(turns_t1) == 1, f"Turn 1: expected 1 turn, got {len(turns_t1)}"
    assert len(comm_msgs_t1) == 1, f"Turn 1: expected 1 comm_message, got {len(comm_msgs_t1)}"
    assert len(comm_threads_t1) == 1, f"Turn 1: expected 1 thread, got {len(comm_threads_t1)}"
    assert len(intents_t1) >= 1, f"Turn 1: expected >=1 intent"
    assert len(candidates_t1) >= 1, f"Turn 1: expected >=1 response candidate"

    turn1 = turns_t1[0]
    assert turn1.data.get("assistant_message") is not None, "Turn 1: assistant_message should be populated"
    assert turn1.data.get("turn_number") == 1, "Turn 1: turn_number should be 1"

    session_id = sessions_t1[0].id

    # ── Turn 2 (continues session by user_ref) ────────────────────────────
    submit_chat_input_fn(g, user_ref="alice@example.com",
                         content="Please draft a brief summary of the deal highlights.")
    rt.run_until_idle()

    sessions_t2 = list(g.objects(type="chat_session"))
    turns_t2 = list(g.objects(type="chat_turn"))

    assert len(sessions_t2) == 1, f"Turn 2: should still have 1 session (resumed), got {len(sessions_t2)}"
    assert len(turns_t2) == 2, f"Turn 2: expected 2 total turns, got {len(turns_t2)}"

    turn2 = turns_t2[-1]
    assert turn2.data.get("turn_number") == 2, "Turn 2: turn_number should be 2"
    assert turn2.data.get("assistant_message") is not None, "Turn 2: assistant_message populated"

    # ── Turn 3 ────────────────────────────────────────────────────────────
    submit_chat_input_fn(g, user_ref="alice@example.com",
                         content="Thanks. What are the next steps we should prioritize?")
    rt.run_until_idle()

    turns_t3 = list(g.objects(type="chat_turn"))
    assert len(turns_t3) == 3, f"Turn 3: expected 3 total turns, got {len(turns_t3)}"

    turn3 = turns_t3[-1]
    assert turn3.data.get("turn_number") == 3, "Turn 3: turn_number should be 3"
    assert turn3.data.get("assistant_message") is not None, "Turn 3: assistant_message populated"

    # ── Verify session metadata ───────────────────────────────────────────
    session = g.get_object(session_id)
    assert session.data.get("turn_count") == 3, \
        f"Session.turn_count should be 3, got {session.data.get('turn_count')}"

    # ── Verify session_contains_turn relations ───────────────────────────
    session_turn_rels = list(g.relations(type="session_contains_turn"))
    assert len(session_turn_rels) == 3, \
        f"Expected 3 session_contains_turn rels, got {len(session_turn_rels)}"
    assert all(r.source == session_id for r in session_turn_rels), \
        "session_contains_turn relations must originate from the session"

    # ── Collect intent observations ───────────────────────────────────────
    all_intents = list(g.objects(type="comm_intent"))
    intent_values = [i.data.get("intent") for i in all_intents]

    return {
        "turns_completed": len(turns_t3),
        "session_id": session_id,
        "session_turn_count": session.data.get("turn_count"),
        "intents_detected": intent_values,
        "comm_threads": len(list(g.objects(type="comm_thread"))),
        "comm_messages": len(list(g.objects(type="comm_message"))),
        "response_candidates": len(list(g.objects(type="comm_response_candidate"))),
        "sources_created": len(list(g.objects(type="source"))),
    }


def run_session_continuity_fixture() -> dict:
    """Test that session resumes correctly when session_id is provided explicitly."""
    g, rt = _make_runtime()

    # First turn — creates session
    submit_chat_input_fn(g, user_ref="bob@example.com", content="Hello!")
    rt.run_until_idle()

    sessions = list(g.objects(type="chat_session"))
    assert len(sessions) == 1
    session_id = sessions[0].id

    # Resume by explicit session_id
    submit_chat_input_fn(g, user_ref="bob@example.com", content="Follow up.",
                         session_id=session_id)
    rt.run_until_idle()

    sessions_after = list(g.objects(type="chat_session"))
    assert len(sessions_after) == 1, "Should still be 1 session after explicit resume"

    turns = list(g.objects(type="chat_turn"))
    assert len(turns) == 2, f"Expected 2 turns after explicit resume, got {len(turns)}"

    return {"sessions": len(sessions_after), "turns": len(turns)}


def run_multi_turn_recall_fixture() -> dict:
    """Conversation memory is graph-native and restart-safe.

    Proves that prior turns are reconstructed from the graph (not from process
    memory): we wipe ALL in-process caches between turns to simulate an
    API-server restart mid-session, then confirm turn 2 still resumes the same
    session AND assembles a ChatContext containing turn 1's exchange.
    """
    g, rt = _make_runtime()

    # ── Turn 1 ────────────────────────────────────────────────────────────
    submit_chat_input_fn(g, user_ref="sam@example.com", content="My name is Sam.")
    rt.run_until_idle()
    session_id = list(g.objects(type="chat_session"))[0].id

    # Simulate an API-server restart: drop every in-process cache. The graph is
    # the only surviving store — if memory weren't graph-native, this would
    # break session continuity and lose the prior turn.
    clear_session_registry()

    # ── Turn 2 — resume by explicit session_id (as the Inspector UI does) ──
    submit_chat_input_fn(g, user_ref="sam@example.com",
                         content="What is my name?", session_id=session_id)
    rt.run_until_idle()

    # Session continuity survived the cache wipe (resolved from the graph).
    sessions = list(g.objects(type="chat_session"))
    assert len(sessions) == 1, f"expected 1 session after restart, got {len(sessions)}"
    turns = sorted(g.objects(type="chat_turn"), key=lambda t: t.data.get("turn_number", 0))
    assert len(turns) == 2, f"expected 2 turns, got {len(turns)}"
    assert turns[-1].data.get("turn_number") == 2, "turn 2 must continue numbering"
    assert turns[-1].data.get("assistant_message") is not None, "turn 2 must be answered"

    # A ChatContext was assembled for turn 2 (and only turn 2 — turn 1 had no
    # prior memory), and it contains turn 1's user message.
    contexts = list(g.objects(type="chat_context"))
    assert len(contexts) == 1, f"expected 1 chat_context, got {len(contexts)}"
    ctx_obj = contexts[0]
    assert ctx_obj.data.get("turn_count") == 1, \
        f"context should include 1 prior turn, got {ctx_obj.data.get('turn_count')}"
    transcript = ctx_obj.data.get("transcript") or ""
    assert "My name is Sam." in transcript, \
        "graph-derived context must include the prior user message"

    # The context is linked to the inbound message (provides_context_for) so the
    # responder's depth-1 view captures it — this edge is what actually carries
    # the prior turns into the LLM prompt.
    ctx_rels = list(g.relations(type="provides_context_for"))
    assert len(ctx_rels) == 1, \
        f"expected 1 provides_context_for rel, got {len(ctx_rels)}"
    assert ctx_rels[0].source == ctx_obj.id, "rel must originate from the chat_context"
    assert ctx_rels[0].target == ctx_obj.data.get("message_id"), \
        "rel must point at the inbound message the context was assembled for"

    return {
        "sessions": len(sessions),
        "turns": len(turns),
        "context_turn_count": ctx_obj.data.get("turn_count"),
    }


def run_bounded_context_fixture() -> dict:
    """max_context_messages bounds how many prior turns enter the context."""
    g = Graph()
    clear_thread_registry()
    clear_session_registry()
    rt = Runtime(g, llm_provider=MockChatProvider())
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(comm_pack, settings=CommunicationSettings())
    rt.load_pack(
        chat_pack,
        settings=ChatSettings(
            llm_provider="mock",
            auto_approve_responses=True,
            max_context_messages=1,  # only the single most recent prior turn
        ),
    )

    session_id = None
    for i in range(3):
        submit_chat_input_fn(g, user_ref="cap@example.com",
                             content=f"Message {i + 1}.", session_id=session_id)
        rt.run_until_idle()
        if session_id is None:
            session_id = list(g.objects(type="chat_session"))[0].id

    # Turns 2 and 3 each assemble a context; with max_context_messages=1 every
    # one must be bounded to a single prior turn (never 2), proving the cap.
    contexts = list(g.objects(type="chat_context"))
    assert len(contexts) == 2, f"expected 2 contexts (turns 2 & 3), got {len(contexts)}"
    assert all(c.data.get("turn_count") == 1 for c in contexts), \
        f"every context must be bounded to 1 turn, got {[c.data.get('turn_count') for c in contexts]}"

    # Boundedness must hold for the actual text the LLM sees, not just the count.
    # Turn 3's context (prior = [turn 2]) must include "Message 2." and must NOT
    # leak the older "Message 1." — proving the cap drops dropped turns entirely.
    turn3_ctx = next(
        (c for c in contexts
         if "Message 2." in (c.data.get("transcript") or "")), None)
    assert turn3_ctx is not None, "expected a context covering the most recent prior turn"
    assert "Message 1." not in (turn3_ctx.data.get("transcript") or ""), \
        "older turn leaked past max_context_messages into the transcript"

    return {"contexts": len(contexts), "bounded_to": 1}


def run_self_knowledge_fixture() -> dict:
    """The assistant can answer "who are you?" using the Agent Profile Pack.

    Proves the wiring at the GRAPH level (the mock LLM returns a fixed canned
    reply, so asserting on reply text would prove nothing): a ProfileContextView
    carrying the assistant's identity is assembled and linked to the inbound
    message via provides_context_for. That edge is exactly what the responder's
    depth-1 view serializes into the prompt, so a live LLM would see the name +
    mission and answer accordingly.

    Also checks the two gates: include_profile=False suppresses injection, and
    system_prompt_override injects the literal override instead of the profile.
    """
    from packs.agent_profile import pack as ap_pack, AgentProfileSettings
    from packs.agent_profile.behaviors import clear_profile_registry
    from packs.agent_profile.tools import register_profile_fn
    from packs.identity_auth import pack as identity_pack, IdentitySettings
    from packs.identity_auth.behaviors import clear_principal_registry
    from packs.identity_auth.tools import register_principal_fn

    def _runtime(chat_settings: ChatSettings):
        clear_thread_registry()
        clear_session_registry()
        clear_profile_registry()
        clear_principal_registry()
        g = Graph()
        rt = Runtime(g, llm_provider=MockChatProvider())
        rt.load_pack(core_pack, settings=CoreSettings())
        rt.load_pack(ap_pack, settings=AgentProfileSettings())
        # Identity determines the profile AUDIENCE: the mission is owner-facing,
        # so the owner must be a registered principal (v0.3 — chat no longer
        # assumes every requester is the owner).
        rt.load_pack(identity_pack, settings=IdentitySettings(
            owner_identifiers=["alice@example.com"],
        ))
        rt.load_pack(comm_pack, settings=CommunicationSettings())
        rt.load_pack(chat_pack, settings=chat_settings)
        register_principal_fn(g, "alice@example.com", role="owner", name="Alice")
        rt.run_until_idle()
        return g, rt

    # ── 1. Profile present + include_profile (default) → identity injected ──
    g, rt = _runtime(ChatSettings(llm_provider="mock", auto_approve_responses=True))
    register_profile_fn(g, name="Aria",
                        mission="Help Alice grow her robotics startup.",
                        owner_name="Alice")
    rt.run_until_idle()

    submit_chat_input_fn(g, user_ref="alice@example.com",
                         content="Who are you and what is your mission?")
    rt.run_until_idle()

    msgs = list(g.objects(type="comm_message"))
    assert len(msgs) == 1, f"expected 1 comm_message, got {len(msgs)}"
    msg_id = msgs[0].id

    views = list(g.objects(type="profile_context_view"))
    assert len(views) == 1, f"expected 1 profile_context_view, got {len(views)}"
    view = views[0]
    assert view.data.get("agent_name") == "Aria", \
        f"view must carry the assistant name, got {view.data.get('agent_name')}"
    assert "robotics startup" in (view.data.get("mission") or ""), \
        "owner-facing view must include the mission"

    # The view is linked to THIS inbound message, so the responder's depth-1
    # view (and thus the LLM prompt) captures it.
    ctx_rels = [r for r in g.relations(type="provides_context_for")
                if r.source == view.id]
    assert len(ctx_rels) == 1, "profile view must link to the inbound message"
    assert ctx_rels[0].target == msg_id, "link must point at the inbound message"

    # The pipeline still produced a reply (mock canned text).
    turns = list(g.objects(type="chat_turn"))
    assert turns and turns[0].data.get("assistant_message"), "turn must be answered"

    # ── 2. include_profile=False → no identity injected ────────────────────
    g2, rt2 = _runtime(ChatSettings(llm_provider="mock", auto_approve_responses=True,
                                    include_profile=False))
    register_profile_fn(g2, name="Aria", mission="Help Alice.", owner_name="Alice")
    rt2.run_until_idle()
    submit_chat_input_fn(g2, user_ref="alice@example.com", content="Who are you?")
    rt2.run_until_idle()
    assert len(list(g2.objects(type="profile_context_view"))) == 0, \
        "include_profile=False must suppress profile injection"

    # ── 3. system_prompt_override → literal override, profile bypassed ─────
    override = "You are a terse status bot. Answer in one line."
    g3, rt3 = _runtime(ChatSettings(llm_provider="mock", auto_approve_responses=True,
                                    system_prompt_override=override))
    register_profile_fn(g3, name="Aria", mission="Help Alice.", owner_name="Alice")
    rt3.run_until_idle()
    submit_chat_input_fn(g3, user_ref="alice@example.com", content="Who are you?")
    rt3.run_until_idle()
    ov_views = list(g3.objects(type="profile_context_view"))
    assert len(ov_views) == 1, f"override must inject 1 context view, got {len(ov_views)}"
    ov = ov_views[0]
    assert ov.data.get("agent_name") == "", "override must NOT carry the profile name"
    assert ov.data.get("standing_instructions") == [override], \
        "override text must be injected as the sole instruction"
    assert (ov.data.get("metadata") or {}).get("origin") == "system_prompt_override"

    # ── 4. override wins over include_profile=False (independent knobs) ─────
    g4, rt4 = _runtime(ChatSettings(llm_provider="mock", auto_approve_responses=True,
                                    include_profile=False,
                                    system_prompt_override=override))
    register_profile_fn(g4, name="Aria", mission="Help Alice.", owner_name="Alice")
    rt4.run_until_idle()
    submit_chat_input_fn(g4, user_ref="alice@example.com", content="Who are you?")
    rt4.run_until_idle()
    ov4 = list(g4.objects(type="profile_context_view"))
    assert len(ov4) == 1, \
        "an explicit override must be honored even when include_profile=False"
    assert ov4[0].data.get("standing_instructions") == [override], \
        "override text must be injected even when include_profile=False"
    assert ov4[0].data.get("agent_name") == "", \
        "override path must not leak the profile name"

    return {
        "agent_name": view.data.get("agent_name"),
        "linked_to_message": ctx_rels[0].target == msg_id,
        "suppressed_when_disabled": True,
        "override_injected": True,
        "override_wins_over_disabled_profile": True,
    }


def run_all() -> bool:
    print("=" * 60)
    print("Chat Adapter Pack Fixtures")
    print("=" * 60)
    all_pass = True

    print("\n[1] 3-turn conversation fixture")
    try:
        result = run_three_turn_conversation_fixture()
        print(f"  PASS: turns={result['turns_completed']}, "
              f"session_turn_count={result['session_turn_count']}, "
              f"intents={result['intents_detected']}")
        print(f"        threads={result['comm_threads']}, "
              f"messages={result['comm_messages']}, "
              f"sources={result['sources_created']}")
    except AssertionError as e:
        print(f"  FAIL: {e}")
        all_pass = False

    print("\n[2] session continuity fixture")
    try:
        result = run_session_continuity_fixture()
        print(f"  PASS: sessions={result['sessions']}, turns={result['turns']}")
    except AssertionError as e:
        print(f"  FAIL: {e}")
        all_pass = False

    print("\n[3] multi-turn recall (graph-native, restart-safe) fixture")
    try:
        result = run_multi_turn_recall_fixture()
        print(f"  PASS: sessions={result['sessions']}, turns={result['turns']}, "
              f"context_turn_count={result['context_turn_count']}")
    except AssertionError as e:
        print(f"  FAIL: {e}")
        all_pass = False

    print("\n[4] bounded context (max_context_messages) fixture")
    try:
        result = run_bounded_context_fixture()
        print(f"  PASS: contexts={result['contexts']}, bounded_to={result['bounded_to']}")
    except AssertionError as e:
        print(f"  FAIL: {e}")
        all_pass = False

    print("\n[5] self-knowledge (who are you? — agent_profile wiring) fixture")
    try:
        result = run_self_knowledge_fixture()
        print(f"  PASS: agent_name={result['agent_name']!r}, "
              f"linked_to_message={result['linked_to_message']}, "
              f"suppressed_when_disabled={result['suppressed_when_disabled']}, "
              f"override_injected={result['override_injected']}")
    except AssertionError as e:
        print(f"  FAIL: {e}")
        all_pass = False

    print(f"\n{'ALL PASS' if all_pass else 'SOME FAILURES'}")
    return all_pass


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
