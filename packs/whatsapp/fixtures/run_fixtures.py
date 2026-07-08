"""Run WhatsApp Adapter Pack fixture scenarios.

End-to-end through the real cross-pack cascade with a mock send capability
and the mock chat LLM — no token, no network, no API key. Inbound uses the
Cloud API's actual webhook envelope shape, so the parser is exercised too.

Usage:
    python packs/whatsapp/fixtures/run_fixtures.py
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
from packs.tool_gateway import pack as tg_pack, ToolGatewaySettings
from packs.tool_gateway.tools import clear_local_registry, register_local_capability
from packs.whatsapp import pack as whatsapp_pack, WhatsAppSettings
from packs.whatsapp.behaviors import clear_seen_messages

OWNER_PHONE = "15550001111"
STRANGER_PHONE = "15559998888"


def _envelope(from_number: str, name: str, text: str, wamid: str) -> dict:
    """A Cloud API webhook envelope, as Meta actually delivers it."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "1234567890",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": "555000111222"},
                    "contacts": [{"wa_id": from_number, "profile": {"name": name}}],
                    "messages": [{
                        "id": wamid,
                        "from": from_number,
                        "timestamp": "1751932800",
                        "type": "text",
                        "text": {"body": text},
                    }],
                },
            }],
        }],
    }


def _runtime(reply_policy: str = "owner_only"):
    clear_session_registry()
    clear_thread_registry()
    clear_principal_registry()
    clear_local_registry()
    clear_seen_messages()

    sent: list[dict] = []
    register_local_capability(
        "whatsapp", "send_message",
        lambda to="", text="", execution_context=None: (
            sent.append({"to": to, "text": text,
                         "credential_injected": bool((execution_context or {}).get("credential"))})
            or {"ok": True, "message_id": f"wamid.out{len(sent)}"}
        ),
    )

    g = Graph()
    rt = Runtime(g, llm_provider=MockChatProvider())
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(tg_pack, settings=ToolGatewaySettings(
        auto_approve_risk_classes=["low", "medium"],
    ))
    rt.load_pack(identity_pack, settings=IdentitySettings(
        owner_identifiers=[f"whatsapp:{OWNER_PHONE}"],
    ))
    rt.load_pack(comm_pack, settings=CommunicationSettings())
    rt.load_pack(chat_pack, settings=ChatSettings(reply_policy=reply_policy))
    rt.load_pack(whatsapp_pack, settings=WhatsAppSettings(phone_number_id="555000111222"))
    register_principal_fn(g, f"whatsapp:{OWNER_PHONE}", role="owner",
                          name="Yohei", channel="whatsapp")
    rt.run_until_idle()
    return g, rt, sent


def run_owner_conversation_fixture() -> dict:
    """Owner webhook → parsed, replied, delivered via a gateway send call."""
    from packs.whatsapp.tools import submit_whatsapp_webhook_fn

    g, rt, sent = _runtime()
    created = submit_whatsapp_webhook_fn(
        g, _envelope(OWNER_PHONE, "Yohei", "What's on my calendar today?", "wamid.abc1"),
    )
    assert len(created) == 1, "envelope parser must yield one message"
    rt.run_until_idle()

    inputs = list(g.objects(type="chat_input"))
    assert len(inputs) == 1 and inputs[0].data["user_ref"] == f"whatsapp:{OWNER_PHONE}"
    msgs = list(g.objects(type="comm_message"))
    assert msgs[0].data["channel"] == "whatsapp"
    assert msgs[0].data["metadata"]["reply_gate"] == "open"

    turns = list(g.objects(type="chat_turn"))
    assert len(turns) == 1 and turns[0].data["assistant_message"]

    calls = [c for c in g.objects(type="capability_call")
             if c.data["capability_name"] == "send_message"]
    assert len(calls) == 1 and calls[0].data["status"] == "done"
    assert calls[0].data["proposed_by"] == "whatsapp_dispatcher"
    assert len(sent) == 1 and sent[0]["to"] == OWNER_PHONE

    return {"turns": 1, "sends": len(sent), "call_status": calls[0].data["status"]}


def run_stranger_deflection_fixture() -> dict:
    """Stranger webhook → deflection template delivered; zero LLM calls."""
    from packs.whatsapp.tools import submit_whatsapp_webhook_fn

    g, rt, sent = _runtime()
    submit_whatsapp_webhook_fn(
        g, _envelope(STRANGER_PHONE, "Rando", "Who do you work for?", "wamid.xyz9"),
    )
    rt.run_until_idle()

    msgs = list(g.objects(type="comm_message"))
    assert msgs[0].data["metadata"]["reply_gate"] == "deflect"
    assert [e for e in g.events if e.type == "llm.requested"] == []
    assert len(sent) == 1 and sent[0]["to"] == STRANGER_PHONE
    assert "only chat with my owner" in sent[0]["text"]

    return {"sends": len(sent), "gated": True}


def run_dedup_fixture() -> dict:
    """The same wamid delivered twice (Meta retries) converses once."""
    from packs.whatsapp.tools import submit_whatsapp_webhook_fn

    g, rt, sent = _runtime()
    env = _envelope(OWNER_PHONE, "Yohei", "hello!", "wamid.dup1")
    submit_whatsapp_webhook_fn(g, env)
    rt.run_until_idle()
    submit_whatsapp_webhook_fn(g, env)
    rt.run_until_idle()

    assert len(list(g.objects(type="chat_input"))) == 1, "dedup must block re-ingest"
    assert len(sent) == 1
    return {"chat_inputs": 1}


def run_non_text_skipped_fixture() -> dict:
    """Non-text messages are skipped by the parser, not half-ingested."""
    from packs.whatsapp.tools import submit_whatsapp_webhook_fn

    g, rt, sent = _runtime()
    env = _envelope(OWNER_PHONE, "Yohei", "ignored", "wamid.img1")
    env["entry"][0]["changes"][0]["value"]["messages"][0]["type"] = "image"
    created = submit_whatsapp_webhook_fn(g, env)
    rt.run_until_idle()

    assert created == []
    assert list(g.objects(type="whatsapp_message")) == []
    assert sent == []
    return {"skipped": True}


def run_all() -> bool:
    print("=" * 60)
    print("WhatsApp Adapter Pack Fixtures")
    print("=" * 60)

    print("\n[1] owner conversation (webhook → reply → gateway delivery)")
    r = run_owner_conversation_fixture()
    print(f"  PASS: turns={r['turns']}, sends={r['sends']}, call={r['call_status']}")

    print("\n[2] stranger deflection (no LLM, polite template delivered)")
    r = run_stranger_deflection_fixture()
    print(f"  PASS: sends={r['sends']}, gated={r['gated']}")

    print("\n[3] webhook dedup (same wamid twice → one conversation)")
    r = run_dedup_fixture()
    print(f"  PASS: chat_inputs={r['chat_inputs']}")

    print("\n[4] non-text messages skipped cleanly")
    r = run_non_text_skipped_fixture()
    print(f"  PASS: skipped={r['skipped']}")

    print("\nALL PASS")
    return True


if __name__ == "__main__":
    try:
        ok = run_all()
    except AssertionError as e:
        print(f"\nFAIL: {e}")
        sys.exit(1)
    sys.exit(0 if ok else 1)
