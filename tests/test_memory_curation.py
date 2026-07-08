"""Memory curation tests — the judgment layer over the (already sound) plumbing.

Acceptance criteria from the upgrade plan:
  - a question produces no memory; the equivalent statement produces one
  - third-party guidance from non-conversational content is rejected WITH a
    written rationale once identity verification is possible
  - category priority relieves the acceptance bar (auto_accept_categories
    finally does something)
  - recall never returns a memory born in the frame that is asking
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import pytest

from activegraph import Graph, Runtime

from packs.chat.behaviors import _classify_chat_memory, clear_session_registry
from packs.communication.behaviors import clear_thread_registry
from packs.core import pack as core_pack, CoreSettings
from packs.core.behaviors import _infer_category
from packs.identity_auth import pack as identity_pack, IdentitySettings
from packs.identity_auth.behaviors import clear_principal_registry
from packs.identity_auth.tools import register_principal_fn
from packs.memory_gateway import pack as memory_pack, MemoryGatewaySettings
from packs.memory_gateway.backend import SqliteMemoryBackend


@pytest.fixture(autouse=True)
def _isolate():
    clear_session_registry()
    clear_thread_registry()
    clear_principal_registry()
    yield
    clear_session_registry()
    clear_thread_registry()
    clear_principal_registry()


# ------------------------------------------------------------------ questions


def test_questions_are_not_memory_candidates():
    # Chat write path: punctuated and bare interrogatives never classify.
    assert _classify_chat_memory("What's my favorite color?") is None
    assert _classify_chat_memory("what's my favorite color") is None
    assert _classify_chat_memory("Should I always use dark mode?") is None
    # The equivalent statements still do.
    assert _classify_chat_memory("My favorite color is green") == "preference"
    assert _classify_chat_memory("Always use dark mode") == "instruction"

    # Core write path: '?' classifies as question BEFORE keyword categories.
    assert _infer_category("Should I always use dark mode?") == "question"
    assert _infer_category("What's my favorite color?") == "question"
    assert _infer_category("You should always use dark mode") == "instruction"


# ------------------------------------------------------------------ provenance


def _rt(identity: bool, mem_settings=None):
    g = Graph()
    rt = Runtime(g)
    rt.load_pack(core_pack, settings=CoreSettings())
    if identity:
        rt.load_pack(identity_pack, settings=IdentitySettings())
    rt.load_pack(memory_pack, settings=mem_settings or MemoryGatewaySettings(
        backend_url=f"file:mem_{uuid.uuid4().hex}?mode=memory&cache=shared",
    ))
    return rt


def test_third_party_email_guidance_rejected_with_rationale():
    rt = _rt(identity=True)
    # Identity verification becomes possible once any principal exists.
    register_principal_fn(rt.graph, "yohei@example.com", role="owner")

    # The report's exact failure case: an inbound email body phrased as a
    # request, extracted by Core's generic path.
    rt.graph.add_object("source", {
        "kind": "email",
        "content": "Please review the attached term sheet for the Series B round.",
        "channel": "email",
        "sender_ref": "founder@northwind.ai",
    })
    rt.run_until_idle()

    evals = [e for e in rt.graph.objects(type="evaluation")
             if e.data.get("subject_type") == "memory_candidate"]
    assert evals, "the candidate must be evaluated, not silently dropped"
    rejected = [e for e in evals if e.data["judgment"] == "rejected"]
    assert rejected, "third-party guidance must be rejected"
    assert "documents don't give orders" in rejected[0].data["rationale"]
    assert list(rt.graph.objects(type="memory_item")) == []


def test_trusted_sender_email_guidance_admitted():
    rt = _rt(identity=True)
    register_principal_fn(rt.graph, "yohei@example.com", role="owner")

    rt.graph.add_object("source", {
        "kind": "email",
        "content": "Please always CC legal on term sheets.",
        "channel": "email",
        "sender_ref": "yohei@example.com",
    })
    rt.run_until_idle()

    items = list(rt.graph.objects(type="memory_item"))
    assert len(items) == 1, "the owner's own guidance is admitted"


def test_without_identity_admission_is_unchanged():
    # No identity pack → verification impossible → pre-v0.2 behavior.
    rt = _rt(identity=False)
    rt.graph.add_object("source", {
        "kind": "email",
        "content": "Please review the attached term sheet.",
        "channel": "email",
        "sender_ref": "founder@northwind.ai",
    })
    rt.run_until_idle()
    accepted = [e for e in rt.graph.objects(type="evaluation")
                if e.data.get("judgment") == "accepted"]
    assert accepted, "graceful degradation: no identity → admit as before"


def test_conversational_sources_always_build_memory():
    rt = _rt(identity=True)
    register_principal_fn(rt.graph, "yohei@example.com", role="owner")
    # A chat_message source from an unregistered speaker: conversations
    # build memory (the reply gate governs who converses; the memory is
    # subject-scoped to the speaker).
    rt.graph.add_object("source", {
        "kind": "chat_message",
        "content": "I prefer bullet points rather than long paragraphs.",
        "channel": "chat",
        "sender_ref": "user:someone",
    })
    rt.run_until_idle()
    items = list(rt.graph.objects(type="memory_item"))
    assert len(items) == 1


# ------------------------------------------------------------------ category relief


def test_priority_categories_accept_at_relieved_threshold():
    rt = _rt(identity=False)
    # 0.55 sits between auto_accept_min_confidence (0.5) and
    # acceptance_threshold (0.6): a priority category passes, a plain one fails.
    rt.graph.add_object("memory_candidate", {
        "text": "Always deploy on Fridays only after the standup.",
        "confidence": 0.55, "category": "instruction",
        "source_ids": [], "observation_ids": [],
    })
    rt.graph.add_object("memory_candidate", {
        "text": "The office coffee machine hisses sometimes.",
        "confidence": 0.55, "category": "fact",
        "source_ids": [], "observation_ids": [],
    })
    rt.run_until_idle()

    by_text = {e.data["rationale"]: e.data["judgment"]
               for e in rt.graph.objects(type="evaluation")}
    judgments = {}
    for e in rt.graph.objects(type="evaluation"):
        cand = rt.graph.get_object(e.data["subject_id"])
        judgments[cand.data["category"]] = e.data["judgment"]
    assert judgments["instruction"] == "accepted"
    assert judgments["fact"] == "rejected"


# ------------------------------------------------------------------ frame guard


def test_recall_never_returns_memories_born_in_the_asking_frame():
    backend = SqliteMemoryBackend(":memory:")
    backend.store_item("m1", "my favorite color is green",
                       metadata={"frame_id": "frame_A"}, subject_ref="u1")
    backend.store_item("m2", "my favorite editor is vim",
                       metadata={"frame_id": "frame_B"}, subject_ref="u1")

    hits = backend.retrieve_by_query("favorite color", subject_ref="u1",
                                     subject_scoped=True, include_global=False,
                                     exclude_frame_id="frame_A")
    assert "m1" not in [h["item_id"] for h in hits]  # same-frame twin invisible

    hits = backend.retrieve_by_query("favorite color", subject_ref="u1",
                                     subject_scoped=True, include_global=False,
                                     exclude_frame_id="frame_C")
    assert "m1" in [h["item_id"] for h in hits]  # other frames recall normally
