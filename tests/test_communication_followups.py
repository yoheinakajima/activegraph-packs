import hashlib

from activegraph import Graph, Runtime

from packs.activity_normalizer import pack as normalizer_pack
from packs.communication import pack as communication_pack
from packs.entity import pack as entity_pack
from packs.semantic_extraction import pack as semantic_pack


def _build():
    graph = Graph(); runtime = Runtime(graph)
    runtime.load_pack(normalizer_pack); runtime.load_pack(semantic_pack)
    runtime.load_pack(entity_pack); runtime.load_pack(communication_pack)
    return graph, runtime


def _evidence(graph, text):
    digest = hashlib.sha256(text.encode()).hexdigest()
    item = graph.add_object("acquired_item", {"source_surface_id": "mail", "provider_item_id": "1", "dedup_key": "1", "source_ref": "mail:1", "source_hash": digest, "provider_time": "2026-07-12T00:00:00Z", "replay_mode": "inline", "replay_payload_ref": text, "replay_payload_hash": digest, "media_type": "text/plain", "importer_id": "test", "importer_version": "1"})
    graph.add_object("acquired_content", {"acquired_item_id": item.id, "normalized_content": text, "normalized_metadata": {"interpretation_family": "conversation"}, "source_category": "communication", "connection_path": "pack", "is_fixture": True})


def _annotation(graph, evidence, exact):
    return graph.add_object("semantic_annotation", {"annotation_identity": f"a-{hashlib.sha256(exact.encode()).hexdigest()}", "evidence_id": evidence.id, "evidence_identity": evidence.data["evidence_identity"], "revision_id": evidence.data["revision_id"], "facet": "relation_mention", "body": {"text": exact, "subject": "sender", "predicate": "requests", "object": "deck"}, "selector": {"kind": "char_span", "exact": exact, "prefix": "", "suffix": "", "start": 0, "end": len(exact)}, "extractor_id": "semantic.llm", "extractor_version": "0.1.0", "config_hash": "0" * 64, "run_id": "run", "confidence": .9, "attribution": "unknown", "author_role": None, "modality": "stated", "polarity": "positive", "event_time": None, "observation_time": None, "status": "active", "invalidation_reason": None, "metadata": {}})


def test_only_explicit_clean_inbound_requests_become_reviewable_tasks():
    graph, runtime = _build(); text = "Could you please send me the deck tomorrow?"
    _evidence(graph, text); runtime.run_until_idle(); evidence = graph.objects(type="activity_evidence")[0]
    graph.add_object("conversation_message", {"message_identity": "m1", "thread_id": "thread", "source_surface_id": "mail", "service": "test", "account_ref": "owner", "provider_message_id": "1", "provider_revision_ref": "1", "internet_message_id": None, "sender": "a@example.com", "recipients": ["owner@example.com"], "cc": [], "subject": "Deck", "sent_at": "2026-07-12T00:00:00Z", "direction": "inbound", "message_kind": "human", "labels": [], "unread": True, "display_content": text, "interpretation_content": text, "interpretation_state": "ready", "suppression_counts": {}, "injection_flags": [], "evidence_id": evidence.id, "refs": [evidence.id], "metadata": {}})
    annotation = _annotation(graph, evidence, text); runtime.run_until_idle()
    [task] = graph.objects(type="task_candidate")
    assert task.data["metadata"]["requires_review"] is True
    assert task.data["metadata"]["annotation_id"] == annotation.id
    assert task.data["evidence_id"] == evidence.id


def test_relation_without_request_cue_does_not_become_task():
    graph, runtime = _build(); text = "Alice sent the deck yesterday."
    _evidence(graph, text); runtime.run_until_idle(); evidence = graph.objects(type="activity_evidence")[0]
    graph.add_object("conversation_message", {"message_identity": "m1", "thread_id": "thread", "source_surface_id": "mail", "service": "test", "account_ref": "owner", "provider_message_id": "1", "provider_revision_ref": "1", "internet_message_id": None, "sender": "a@example.com", "recipients": ["owner@example.com"], "cc": [], "subject": "Deck", "sent_at": "2026-07-12T00:00:00Z", "direction": "inbound", "message_kind": "human", "labels": [], "unread": True, "display_content": text, "interpretation_content": text, "interpretation_state": "ready", "suppression_counts": {}, "injection_flags": [], "evidence_id": evidence.id, "refs": [evidence.id], "metadata": {}})
    _annotation(graph, evidence, text); runtime.run_until_idle()
    assert not graph.objects(type="task_candidate")
