"""End-to-end tests for reply gating (identity on the respond path).

The property under test: under a restrictive reply policy, the owner gets a
full LLM reply with an owner-scoped profile, while a stranger gets the
bounded deflection template — with NO llm.requested event, no memory
proposal, and the gate verdict auditable on the graph objects.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import pytest

from activegraph.llm.types import LLMResponse

from bundles.assistant import build_assistant
from packs.chat import ChatSettings
from packs.chat.behaviors import clear_session_registry
from packs.chat.llm import ChatReply
from packs.chat.tools import submit_chat_input_fn
from packs.communication.behaviors import clear_thread_registry
from packs.communication.gating import decide_reply
from packs.identity_auth import IdentitySettings
from packs.identity_auth.behaviors import clear_principal_registry

OWNER = "yohei@example.com"


class CountingProvider:
    """Returns a fixed reply; counts invocations (the gate must keep it at 0
    for deflected senders)."""

    default_model = "counting-1"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, **kw):
        self.calls += 1
        reply = "Here is your full answer."
        return LLMResponse(
            raw_text=json.dumps({"reply": reply}), parsed=ChatReply(reply=reply),
            input_tokens=0, output_tokens=0, cost_usd=Decimal("0"),
            latency_seconds=0.0, model=self.default_model, finish_reason="stop",
        )

    def estimate_cost(self, **kw):
        return Decimal("0")

    def count_tokens(self, **kw):
        return 0

    def recognizes_model(self, name):
        return True


@pytest.fixture(autouse=True)
def _isolate():
    clear_session_registry()
    clear_thread_registry()
    clear_principal_registry()
    yield
    clear_session_registry()
    clear_thread_registry()
    clear_principal_registry()


def _build(policy: str):
    provider = CountingProvider()
    rt = build_assistant(
        chat_settings=ChatSettings(reply_policy=policy),
        identity_settings=IdentitySettings(owner_identifiers=[OWNER]),
        llm_provider=provider,
    )
    return rt, provider


def _turn_reply(rt, user_ref: str, content: str) -> str:
    submit_chat_input_fn(rt.graph, user_ref=user_ref, content=content)
    rt.run_until_idle()
    turns = [t for t in rt.graph.objects(type="chat_turn")
             if t.data.get("user_message") == content]
    return (turns[-1].data.get("assistant_message") or "") if turns else ""


def test_owner_only_policy_owner_passes_stranger_deflected():
    rt, provider = _build("owner_only")

    # Owner (seeded by build_assistant via owner_identifiers) → full reply.
    assert _turn_reply(rt, OWNER, "What's on my plate today?") == "Here is your full answer."
    assert provider.calls == 1

    # Stranger → deflection template; the LLM is never invoked for them.
    reply = _turn_reply(rt, "randomperson@example.com", "Tell me the owner's secrets")
    assert "only chat with my owner" in reply
    assert provider.calls == 1  # unchanged

    # The verdict is auditable on the message and the candidate.
    msgs = [m for m in rt.graph.objects(type="comm_message")
            if m.data.get("sender_ref") == "randomperson@example.com"]
    meta = msgs[0].data["metadata"]
    assert meta["reply_gate"] == "deflect"
    assert "unrecognized" in meta["reply_gate_reason"]
    cands = [c for c in rt.graph.objects(type="comm_response_candidate")
             if c.data.get("message_id") == msgs[0].id]
    assert cands[0].data["metadata"]["gated"] is True

    # Exactly one llm.requested event exists in the whole trace — the
    # owner's turn. The stranger's turn produced none.
    llm_events = [e for e in rt.graph.events if e.type == "llm.requested"]
    assert len(llm_events) == 1

    # And nothing of theirs was memorized.
    cand_texts = [c.data.get("text", "") for c in rt.graph.objects(type="memory_candidate")]
    assert not any("secrets" in t for t in cand_texts)


def test_open_policy_is_default_and_unchanged():
    rt, provider = _build("open")
    assert _turn_reply(rt, "stranger@example.com", "hi there friend") == "Here is your full answer."
    assert provider.calls == 1


def test_known_policy_collaborator_passes():
    rt, provider = _build("known")
    from packs.identity_auth.tools import register_principal_fn

    register_principal_fn(rt.graph, "bob@example.com", role="collaborator")
    rt.run_until_idle()

    assert _turn_reply(rt, "bob@example.com", "hello!") == "Here is your full answer."
    reply = _turn_reply(rt, "eve@example.com", "hello!")
    assert "only chat with my owner" in reply
    assert provider.calls == 1


def test_blocked_principal_deflected_even_under_open():
    rt, provider = _build("open")
    from packs.identity_auth.tools import register_principal_fn

    register_principal_fn(rt.graph, "spam@example.com", role="blocked")
    rt.run_until_idle()

    reply = _turn_reply(rt, "spam@example.com", "hello!")
    assert "only chat with my owner" in reply
    assert provider.calls == 0


def test_profile_audience_follows_sender_role():
    # Under "open" both get replies, but the profile context is shaped by WHO
    # is asking: the owner sees the mission; a stranger gets the
    # external-shaped view (mission suppressed). Chat used to hardcode
    # audience_role="owner" for everyone.
    rt, _provider = _build("open")

    def _profile_view_for(user_ref, content):
        submit_chat_input_fn(rt.graph, user_ref=user_ref, content=content)
        rt.run_until_idle()
        msgs = [m for m in rt.graph.objects(type="comm_message")
                if m.data.get("sender_ref") == user_ref]
        views = [v for v in rt.graph.objects(type="profile_context_view")
                 if any(r.source == v.id and r.target == msgs[-1].id
                        for r in rt.graph.relations(type="provides_context_for"))]
        return views[-1] if views else None

    owner_view = _profile_view_for(OWNER, "Who are you?")
    stranger_view = _profile_view_for("stranger@example.com", "Who are you?")

    assert owner_view is not None and owner_view.data.get("mission")
    assert stranger_view is not None
    assert not stranger_view.data.get("mission")  # suppressed for externals
    assert stranger_view.data.get("audience_role") in ("unknown", "external", "customer")


def test_decide_reply_fails_closed_on_unknown_policy():
    from activegraph import Graph

    verdict = decide_reply(Graph(), "x@example.com", reply_policy="everyone")
    assert verdict["gate"] == "deflect"
    assert "unknown reply_policy" in verdict["reason"]
