"""Communication Pack fixtures.

Tests:
  1. intent_detector — classifies query, request, reply, notification, approval_request
  2. thread_tracker  — creates CommThread on first message, updates on subsequent
  3. response_dispatcher — fires on approved candidate, marks sent
  4. Cross-channel: chat + email messages on separate threads
"""

from __future__ import annotations

import sys

from activegraph import Graph, Runtime

from packs.core import pack as core_pack, CoreSettings
from packs.communication import pack as comm_pack, CommunicationSettings
from packs.communication.behaviors import clear_thread_registry
from packs.communication.tools import (
    approve_response_fn,
    create_comm_message_fn,
    create_response_candidate_fn,
)


def run_intent_detection_fixture() -> dict:
    """Test intent_detector classifies all major intent types correctly."""
    clear_thread_registry()

    g = Graph()
    rt = Runtime(g)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(comm_pack, settings=CommunicationSettings())

    test_cases = [
        ("What is the status of the deal?", "query"),
        ("Please draft a reply to the founder email.", "request"),
        ("Thanks for the context. As discussed, I'll proceed.", "reply"),
        ("FYI, the board meeting is scheduled for Friday.", "notification"),
        ("I need your approval on the outbound draft. Requesting approval to send.", "approval_request"),
        ("Can you review the investment memo?", "review"),
    ]

    results = {}
    for content, expected_intent in test_cases:
        create_comm_message_fn(g, channel="chat", content=content,
                               sender_ref="alice@example.com", direction="inbound")
        rt.run_until_idle()

        intents = [o for o in g.objects(type="comm_intent")]
        if intents:
            detected = intents[-1].data.get("intent")
            results[content[:30]] = {
                "expected": expected_intent,
                "detected": detected,
                "pass": detected == expected_intent,
            }

    return results


def run_thread_tracker_fixture() -> dict:
    """Test thread_tracker creates and resumes CommThread objects."""
    clear_thread_registry()

    g = Graph()
    rt = Runtime(g)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(comm_pack, settings=CommunicationSettings())

    # First message — should create a new thread
    create_comm_message_fn(g, channel="chat", content="Hello",
                           sender_ref="user@example.com", direction="inbound")
    rt.run_until_idle()

    threads_after_first = list(g.objects(type="comm_thread"))
    assert len(threads_after_first) == 1, f"Expected 1 thread, got {len(threads_after_first)}"
    thread_id = threads_after_first[0].id
    assert threads_after_first[0].data.get("message_count") == 1

    # Second message (same user, same channel) — should update existing thread
    create_comm_message_fn(g, channel="chat", content="Follow-up question",
                           sender_ref="user@example.com", direction="inbound")
    rt.run_until_idle()

    threads_after_second = list(g.objects(type="comm_thread"))
    assert len(threads_after_second) == 1, f"Expected still 1 thread, got {len(threads_after_second)}"

    # Email message on different channel — creates a separate thread
    create_comm_message_fn(g, channel="email", content="Re: our meeting",
                           sender_ref="founder@startup.com", direction="inbound")
    rt.run_until_idle()

    all_threads = list(g.objects(type="comm_thread"))
    assert len(all_threads) == 2, f"Expected 2 threads (chat + email), got {len(all_threads)}"

    channels = {t.data.get("channel") for t in all_threads}
    assert channels == {"chat", "email"}, f"Expected chat + email channels, got {channels}"

    # Participants created
    participants = list(g.objects(type="comm_participant"))
    assert len(participants) >= 2, f"Expected >=2 participants, got {len(participants)}"

    return {
        "threads_created": len(all_threads),
        "channels": list(channels),
        "participants": len(participants),
    }


def run_response_dispatcher_fixture() -> dict:
    """Test response_dispatcher fires on approved candidate and marks sent."""
    clear_thread_registry()

    g = Graph()
    rt = Runtime(g)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(comm_pack, settings=CommunicationSettings())

    msg = create_comm_message_fn(g, channel="chat", content="What are the next steps?",
                                 sender_ref="user@example.com", direction="inbound")
    rt.run_until_idle()

    thread_id = msg.data.get("thread_id")

    # Create an approved candidate directly — response_dispatcher fires on object.created
    # (patch_object does not re-trigger behaviors, so we create with status=approved)
    candidate = create_response_candidate_fn(
        g,
        message_id=msg.id,
        channel="chat",
        content="Here are the next steps...",
        thread_id=thread_id,
        status="approved",
    )
    rt.run_until_idle()

    # Verify dispatcher fired → status patched to sent
    updated = g.get_object(candidate.id)
    status = updated.data.get("status")
    assert status == "sent", f"Expected status='sent', got '{status}'"

    rels = list(g.relations())
    dispatched_rels = [r for r in rels if r.type == "dispatched_to"]
    assert len(dispatched_rels) >= 1, "Expected dispatched_to relation"

    return {
        "candidate_final_status": status,
        "dispatched_to_relations": len(dispatched_rels),
    }


def run_all() -> bool:
    print("=" * 60)
    print("Communication Pack Fixtures")
    print("=" * 60)
    all_pass = True

    print("\n[1] intent_detector fixture")
    results = run_intent_detection_fixture()
    for label, r in results.items():
        status = "PASS" if r["pass"] else "FAIL"
        print(f"  {status}: '{label}...' → expected={r['expected']}, got={r['detected']}")
        if not r["pass"]:
            all_pass = False

    print("\n[2] thread_tracker fixture")
    result = run_thread_tracker_fixture()
    print(f"  PASS: threads={result['threads_created']}, "
          f"channels={result['channels']}, participants={result['participants']}")

    print("\n[3] response_dispatcher fixture")
    result = run_response_dispatcher_fixture()
    print(f"  PASS: final_status={result['candidate_final_status']}, "
          f"dispatched_to_rels={result['dispatched_to_relations']}")

    print(f"\n{'ALL PASS' if all_pass else 'SOME FAILURES'}")
    return all_pass


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
