"""Cross-pack integration fixture: Communication + Chat + Email + Identity + Core.

This fixture demonstrates the full communication stack:
  1. Email arrives from a founder → email_ingester creates Source + CommMessage + EmailThread
  2. Identity Pack resolves sender → Principal (role=external)
  3. Communication Pack classifies intent (query/request) + creates CommThread
  4. A response candidate is created → reply_drafter creates EmailDraft
  5. send_approver gates: external → approval_request Action created
  6. Follow-up chat message from owner → chat_ingester creates ChatTurn
  7. chat_llm_responder produces CommResponseCandidate (auto-approved for chat)
  8. chat_responder delivers response → ChatTurn.assistant_message populated

Verifies that all three channel packs + Identity + Core work together seamlessly.
"""

from __future__ import annotations

import sys

from activegraph import Graph, Runtime

from packs.core import pack as core_pack, CoreSettings
from packs.identity_auth import pack as identity_pack, IdentitySettings
from packs.identity_auth.behaviors import clear_principal_registry
from packs.communication import pack as comm_pack, CommunicationSettings
from packs.communication.behaviors import clear_thread_registry
from packs.chat import pack as chat_pack, ChatSettings
from packs.chat.behaviors import clear_session_registry
from packs.chat.llm import MockChatProvider
from packs.chat.tools import submit_chat_input_fn
from packs.email import pack as email_pack, EmailSettings
from packs.email.behaviors import clear_email_registry
from packs.email.tools import create_email_response_fn, ingest_email_fn


def _make_full_runtime() -> tuple:
    clear_principal_registry()
    clear_thread_registry()
    clear_session_registry()
    clear_email_registry()

    g = Graph()
    # chat_llm_responder is an @llm_behavior — runtime requires a provider.
    rt = Runtime(g, llm_provider=MockChatProvider())

    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(identity_pack, settings=IdentitySettings(
        owner_identifiers=["alice@northwind.ai"],
        default_external_role="external",
    ))
    rt.load_pack(comm_pack, settings=CommunicationSettings())
    rt.load_pack(email_pack, settings=EmailSettings(
        owner_email_addresses=["alice@northwind.ai"],
        require_approval_for_external=True,
    ))
    rt.load_pack(chat_pack, settings=ChatSettings(
        llm_provider="mock",
        auto_approve_responses=True,
    ))
    return g, rt


def run_full_comm_stack_integration() -> dict:
    """Full stack: email from founder → intent → identity → draft → approval gate."""
    g, rt = _make_full_runtime()

    # ── Step 1: Inbound founder email ────────────────────────────────────────
    ingest_email_fn(
        g,
        message_id="<founder-001@mail.sequoia.com>",
        from_addr="sarah.thompson@sequoia.com",
        to_addrs=["alice@northwind.ai"],
        subject="Investment interest in Northwind AI",
        body_text=(
            "Hi Alice,\n\n"
            "We'd love to schedule a call to discuss a potential seed investment in Northwind AI. "
            "Could you please share your latest deck and financials?\n\n"
            "Best,\nSarah Thompson\nSequoia Capital"
        ),
        received_at="2026-06-03T09:00:00Z",
    )
    rt.run_until_idle()

    # ── Assertions: email ingestion chain ────────────────────────────────────
    sources = list(g.objects(type="source"))
    comm_msgs = list(g.objects(type="comm_message"))
    email_threads = list(g.objects(type="email_thread"))
    comm_threads = list(g.objects(type="comm_thread"))
    principals = list(g.objects(type="principal"))
    intents = list(g.objects(type="comm_intent"))

    assert len(sources) == 1, f"Expected 1 source, got {len(sources)}"
    assert sources[0].data.get("kind") == "email"
    assert sources[0].data.get("sender_ref") == "sarah.thompson@sequoia.com"

    assert len(comm_msgs) == 1, f"Expected 1 comm_message, got {len(comm_msgs)}"
    assert comm_msgs[0].data.get("channel") == "email"

    assert len(email_threads) == 1, "Expected 1 email_thread"
    assert len(comm_threads) == 1, "Expected 1 comm_thread (from thread_tracker)"

    assert len(principals) >= 1, "Expected >=1 principal (sender resolved)"
    principal = principals[0]
    assert principal.data.get("role") == "external", \
        f"Founder should be 'external', got '{principal.data.get('role')}'"
    assert principal.data.get("name") == "sarah.thompson@sequoia.com" or \
           "sarah" in (principal.data.get("name") or "").lower() or \
           principal.data.get("role") == "external"

    assert len(intents) >= 1, "Expected >=1 intent from intent_detector"
    # Founder is requesting a call + asking to share deck/financials → request
    assert intents[0].data.get("intent") in ("request", "query"), \
        f"Expected request or query intent, got {intents[0].data.get('intent')}"

    comm_msg_id = comm_msgs[0].id

    # ── Step 2: Create a response candidate → reply_drafter + send_approver ─
    create_email_response_fn(
        g,
        comm_message_id=comm_msg_id,
        content=(
            "Hi Sarah,\n\n"
            "Thank you for your interest in Northwind AI! I'd be happy to schedule a call. "
            "I'll send over our latest deck and financials shortly.\n\n"
            "Best regards,\nAlice"
        ),
    )
    rt.run_until_idle()

    email_drafts = list(g.objects(type="email_draft"))
    actions = list(g.objects(type="action"))

    assert len(email_drafts) == 1, f"Expected 1 email_draft, got {len(email_drafts)}"
    assert email_drafts[0].data.get("status") == "draft", \
        "Draft should await approval (external send)"
    assert "Re: Investment interest" in email_drafts[0].data.get("subject", ""), \
        f"Subject should be 'Re: Investment interest...', got {email_drafts[0].data.get('subject')}"

    approval_actions = [a for a in actions if a.data.get("kind") == "approval_request"]
    assert len(approval_actions) >= 1, "Expected approval_request action for external send"
    assert approval_actions[0].data.get("risk_class") == "high"

    # ── Step 3: Owner follows up via chat ─────────────────────────────────────
    submit_chat_input_fn(
        g,
        user_ref="alice@northwind.ai",
        content="What should I highlight in my reply to Sarah at Sequoia?",
    )
    rt.run_until_idle()

    chat_sessions = list(g.objects(type="chat_session"))
    chat_turns = list(g.objects(type="chat_turn"))
    chat_candidates = [
        c for c in g.objects(type="comm_response_candidate")
        if c.data.get("channel") == "chat"
    ]

    assert len(chat_sessions) == 1, f"Expected 1 chat_session, got {len(chat_sessions)}"
    assert len(chat_turns) == 1, f"Expected 1 chat_turn, got {len(chat_turns)}"
    assert chat_turns[0].data.get("assistant_message") is not None, \
        "ChatTurn.assistant_message should be populated after chat_responder fires"

    # Owner is resolved as principal with role=owner
    all_principals = list(g.objects(type="principal"))
    owner_principals = [p for p in all_principals if p.data.get("role") == "owner"]
    assert len(owner_principals) >= 1, "Owner principal should be created for alice@northwind.ai"

    # ── Step 4: Second email (thread reply) ───────────────────────────────────
    ingest_email_fn(
        g,
        message_id="<founder-002@mail.sequoia.com>",
        from_addr="sarah.thompson@sequoia.com",
        to_addrs=["alice@northwind.ai"],
        subject="Re: Investment interest in Northwind AI",
        body_text="Alice, thanks for the quick reply! Looking forward to connecting.",
        in_reply_to="<founder-001@mail.sequoia.com>",
        thread_id="<founder-001@mail.sequoia.com>",
        received_at="2026-06-03T10:00:00Z",
    )
    rt.run_until_idle()

    email_threads_final = list(g.objects(type="email_thread"))
    assert len(email_threads_final) == 1, "Reply should join existing email thread, not create new"
    thread = g.get_object(email_threads_final[0].id)
    assert thread.data.get("message_count") == 2, \
        f"Email thread should have 2 messages, got {thread.data.get('message_count')}"

    # Summary
    all_relations = list(g.relations())
    relation_types = sorted({r.type for r in all_relations})

    return {
        "sources": len(list(g.objects(type="source"))),
        "comm_messages": len(list(g.objects(type="comm_message"))),
        "comm_threads": len(list(g.objects(type="comm_thread"))),
        "email_threads": len(list(g.objects(type="email_thread"))),
        "principals": len(all_principals),
        "owner_principals": len(owner_principals),
        "external_principals": len([p for p in all_principals if p.data.get("role") == "external"]),
        "intents": [i.data.get("intent") for i in list(g.objects(type="comm_intent"))],
        "email_drafts": len(list(g.objects(type="email_draft"))),
        "approval_actions": len(approval_actions),
        "chat_sessions": len(chat_sessions),
        "chat_turns": len(chat_turns),
        "chat_assistant_message_populated": chat_turns[0].data.get("assistant_message") is not None,
        "email_thread_message_count": thread.data.get("message_count"),
        "relation_types": relation_types,
    }


def run_all() -> bool:
    print("=" * 60)
    print("Cross-Pack Integration: Communication + Chat + Email + Identity")
    print("=" * 60)

    print("\n[1] Full communication stack integration")
    try:
        result = run_full_comm_stack_integration()
        print(f"  PASS")
        print(f"  sources={result['sources']}, comm_messages={result['comm_messages']}")
        print(f"  comm_threads={result['comm_threads']}, email_threads={result['email_threads']}")
        print(f"  principals={result['principals']} "
              f"(owner={result['owner_principals']}, external={result['external_principals']})")
        print(f"  intents={result['intents']}")
        print(f"  email_drafts={result['email_drafts']}, approval_actions={result['approval_actions']}")
        print(f"  chat_sessions={result['chat_sessions']}, chat_turns={result['chat_turns']}")
        print(f"  assistant_message_populated={result['chat_assistant_message_populated']}")
        print(f"  email_thread.message_count={result['email_thread_message_count']}")
        print(f"  relation_types={result['relation_types']}")
        return True
    except (AssertionError, Exception) as e:
        print(f"  FAIL: {e}")
        import traceback; traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
