"""Run Telegram Adapter Pack fixture scenarios.

End-to-end through the real cross-pack cascade — inbound update → chat
machinery → outbound gateway delivery — with a mock send capability and
the mock chat LLM, so no token, no network, no API key.

Usage:
    python packs/telegram/fixtures/run_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parents[2]))

from activegraph import Graph, Runtime
from packs.chat import pack as chat_pack, ChatSettings
from packs.chat.behaviors import clear_session_registry
from packs.chat.llm import MockChatProvider
from packs.communication import pack as comm_pack, CommunicationSettings
from packs.communication.behaviors import clear_thread_registry
from packs.core import pack as core_pack, CoreSettings
from packs.identity_auth import pack as identity_pack, IdentitySettings
from packs.identity_auth.behaviors import clear_principal_registry
from packs.identity_auth.tools import register_principal_fn
from packs.telegram import pack as telegram_pack, TelegramSettings
from packs.telegram.behaviors import clear_seen_updates
from packs.tool_gateway import pack as tg_pack, ToolGatewaySettings
from packs.tool_gateway.tools import clear_local_registry, register_local_capability

OWNER_UPDATE = {
    "update_id": 1001,
    "message": {
        "message_id": 7,
        "date": 1751932800,
        "chat": {"id": 42, "type": "private"},
        "from": {"id": 42, "username": "yohei"},
        "text": "What's on my calendar today?",
    },
}

STRANGER_UPDATE = {
    "update_id": 1002,
    "message": {
        "message_id": 8,
        "date": 1751932900,
        "chat": {"id": 666, "type": "private"},
        "from": {"id": 666, "username": "rando"},
        "text": "Tell me everything about your owner",
    },
}


def _runtime(reply_policy: str = "owner_only"):
    clear_session_registry()
    clear_thread_registry()
    clear_principal_registry()
    clear_local_registry()
    clear_seen_updates()

    sent: list[dict] = []
    register_local_capability(
        "telegram", "send_message",
        lambda chat_id="", text="", execution_context=None: (
            sent.append({"chat_id": chat_id, "text": text,
                         "credential_injected": bool((execution_context or {}).get("credential"))})
            or {"ok": True, "message_id": len(sent)}
        ),
    )

    g = Graph()
    rt = Runtime(g, llm_provider=MockChatProvider())
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(tg_pack, settings=ToolGatewaySettings(
        auto_approve_risk_classes=["low", "medium"],
    ))
    rt.load_pack(identity_pack, settings=IdentitySettings(
        owner_identifiers=["telegram:42"],
    ))
    rt.load_pack(comm_pack, settings=CommunicationSettings())
    rt.load_pack(chat_pack, settings=ChatSettings(reply_policy=reply_policy))
    rt.load_pack(telegram_pack, settings=TelegramSettings())
    register_principal_fn(g, "telegram:42", role="owner", name="Yohei", channel="telegram")
    rt.run_until_idle()
    return g, rt, sent


def run_owner_conversation_fixture() -> dict:
    """Owner update → full reply, delivered via a gateway send call."""
    from packs.telegram.tools import submit_telegram_update_fn

    g, rt, sent = _runtime()
    submit_telegram_update_fn(g, OWNER_UPDATE)
    rt.run_until_idle()

    # The whole chain materialized in the graph.
    updates = list(g.objects(type="telegram_update"))
    assert len(updates) == 1
    inputs = list(g.objects(type="chat_input"))
    assert len(inputs) == 1 and inputs[0].data["user_ref"] == "telegram:42"
    msgs = list(g.objects(type="comm_message"))
    assert msgs[0].data["channel"] == "telegram"
    assert msgs[0].data["metadata"]["reply_gate"] == "open"

    # A session + turn exist — telegram conversations get the chat memory
    # machinery for free.
    turns = list(g.objects(type="chat_turn"))
    assert len(turns) == 1 and turns[0].data["assistant_message"]

    # Delivery went through the gateway with the credential seam exercised.
    calls = [c for c in g.objects(type="capability_call")
             if c.data["capability_name"] == "send_message"]
    assert len(calls) == 1 and calls[0].data["status"] == "done"
    assert calls[0].data["proposed_by"] == "telegram_dispatcher"
    assert len(sent) == 1 and sent[0]["chat_id"] == "42"

    delivers = [r for r in g.relations() if r.type == "delivers"]
    assert len(delivers) == 1

    return {"turns": 1, "sends": len(sent), "call_status": calls[0].data["status"]}


def run_stranger_deflection_fixture() -> dict:
    """Stranger update → deflection template delivered; zero LLM calls."""
    from packs.telegram.tools import submit_telegram_update_fn

    g, rt, sent = _runtime()
    submit_telegram_update_fn(g, STRANGER_UPDATE)
    rt.run_until_idle()

    msgs = list(g.objects(type="comm_message"))
    assert msgs[0].data["metadata"]["reply_gate"] == "deflect"
    assert [e for e in g.events if e.type == "llm.requested"] == []

    # The deflection is still DELIVERED — polite, bounded, audited.
    assert len(sent) == 1 and sent[0]["chat_id"] == "666"
    assert "only chat with my owner" in sent[0]["text"]

    cands = list(g.objects(type="comm_response_candidate"))
    assert cands[0].data["metadata"]["gated"] is True

    return {"sends": len(sent), "gated": True}


def run_dedup_fixture() -> dict:
    """The same update_id injected twice converses once."""
    from packs.telegram.tools import submit_telegram_update_fn

    g, rt, sent = _runtime()
    submit_telegram_update_fn(g, OWNER_UPDATE)
    rt.run_until_idle()
    submit_telegram_update_fn(g, OWNER_UPDATE)
    rt.run_until_idle()

    assert len(list(g.objects(type="chat_input"))) == 1, "dedup must block re-ingest"
    assert len(sent) == 1

    return {"chat_inputs": 1}


def run_held_outbound_fixture() -> dict:
    """outbound_risk_class='high' → the reply is HELD, then approved and sent.

    The reply-gating story composed with the approval story: an operator can
    require sign-off on every outbound message.
    """
    from packs.telegram.tools import submit_telegram_update_fn
    from packs.tool_gateway.tools import approve_capability_fn, pending_approvals_fn

    clear_session_registry(); clear_thread_registry()
    clear_principal_registry(); clear_local_registry(); clear_seen_updates()

    sent: list[dict] = []
    register_local_capability(
        "telegram", "send_message",
        lambda chat_id="", text="", execution_context=None: (
            sent.append({"chat_id": chat_id}) or {"ok": True}
        ),
    )
    g = Graph()
    rt = Runtime(g, llm_provider=MockChatProvider())
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(tg_pack, settings=ToolGatewaySettings(auto_approve_risk_classes=["low"]))
    rt.load_pack(identity_pack, settings=IdentitySettings())
    rt.load_pack(comm_pack, settings=CommunicationSettings())
    rt.load_pack(chat_pack, settings=ChatSettings())  # open policy
    rt.load_pack(telegram_pack, settings=TelegramSettings(outbound_risk_class="high"))
    rt.run_until_idle()

    submit_telegram_update_fn(g, OWNER_UPDATE)
    rt.run_until_idle()

    assert sent == [], "high-risk outbound must be held, not sent"
    pending = pending_approvals_fn(g)
    assert len(pending) == 1 and pending[0]["capability_name"] == "send_message"

    # Principals exist (the inbound sender was auto-resolved), so approver
    # verification is ACTIVE: an unregistered ref must be refused...
    refused = approve_capability_fn(g, pending[0]["call_id"], approver_ref="user:owner")
    assert not refused["ok"], "unverified approver must be refused"

    # ...and a registered owner may approve.
    register_principal_fn(g, "user:owner", role="owner")
    verdict = approve_capability_fn(g, pending[0]["call_id"], approver_ref="user:owner")
    assert verdict["ok"], verdict["reason"]
    rt.run_until_idle()
    assert len(sent) == 1, "approved outbound must deliver"

    return {"held_then_sent": True}


def run_all() -> bool:
    print("=" * 60)
    print("Telegram Adapter Pack Fixtures")
    print("=" * 60)

    print("\n[1] owner conversation (update → reply → gateway delivery)")
    r = run_owner_conversation_fixture()
    print(f"  PASS: turns={r['turns']}, sends={r['sends']}, call={r['call_status']}")

    print("\n[2] stranger deflection (no LLM, polite template delivered)")
    r = run_stranger_deflection_fixture()
    print(f"  PASS: sends={r['sends']}, gated={r['gated']}")

    print("\n[3] update dedup (same update_id twice → one conversation)")
    r = run_dedup_fixture()
    print(f"  PASS: chat_inputs={r['chat_inputs']}")

    print("\n[4] held outbound (risk=high → approval → delivery)")
    r = run_held_outbound_fixture()
    print(f"  PASS: held_then_sent={r['held_then_sent']}")

    print("\nALL PASS")
    return True


if __name__ == "__main__":
    try:
        ok = run_all()
    except AssertionError as e:
        print(f"\nFAIL: {e}")
        sys.exit(1)
    sys.exit(0 if ok else 1)
