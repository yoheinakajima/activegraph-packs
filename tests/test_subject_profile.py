from activegraph import Graph, Runtime

from packs.activity_normalizer import pack as normalizer_pack
from packs.subject_profile import pack as subject_profile_pack
from packs.subject_profile.tools import forget_subject_fact_fn, review_subject_fact_fn


def _runtime():
    graph = Graph()
    runtime = Runtime(graph)
    runtime.load_pack(normalizer_pack)
    runtime.load_pack(subject_profile_pack)
    return graph, runtime


def _evidence(graph, *, scope="owner_profile"):
    text = "Yohei builds ActiveGraph."
    digest = __import__("hashlib").sha256(text.encode()).hexdigest()
    item = graph.add_object("acquired_item", {"source_surface_id": "public_presence", "provider_item_id": "1", "dedup_key": "1", "source_ref": "https://example.test", "source_hash": digest, "provider_time": None, "replay_mode": "inline", "replay_payload_ref": text, "replay_payload_hash": digest, "media_type": "text/plain", "importer_id": "test", "importer_version": "1"})
    graph.add_object("acquired_content", {"acquired_item_id": item.id, "normalized_content": text, "normalized_metadata": {"subject_scope": scope}, "source_category": "local_knowledge", "connection_path": "pack", "is_fixture": True})


def _candidate(graph, evidence, value="ActiveGraph"):
    return graph.add_object("profile_candidate", {"candidate_identity": f"c-{value}", "text": f"Yohei builds {value}.", "confidence": .8, "evidence_id": evidence.id, "evidence_identity": evidence.data["evidence_identity"], "revision_id": evidence.data["revision_id"], "extraction_record_id": "run", "extractor_id": "test", "extractor_version": "1", "extraction_config_id": "cfg", "status": "candidate", "invalidation_reason": None, "metadata": {"annotation_id": "annotation-1"}, "attribute": "project", "value": value})


def test_candidate_requires_explicit_verdict_and_preserves_evidence():
    graph, runtime = _runtime(); _evidence(graph); runtime.run_until_idle()
    evidence = graph.objects(type="activity_evidence")[0]
    candidate = _candidate(graph, evidence)
    runtime.run_until_idle()
    assert not graph.objects(type="subject_fact")
    result = review_subject_fact_fn(graph, candidate.id, "confirm")
    runtime.run_until_idle()
    [fact] = graph.objects(type="subject_fact")
    assert fact.data["evidence_id"] == evidence.id
    assert fact.data["source_surface_id"] == "public_presence"
    assert graph.get_object(result["verdict_id"]).data["status"] == "applied"


def test_connector_content_cannot_become_owner_fact_without_owner_scope():
    graph, runtime = _runtime(); _evidence(graph, scope=None); runtime.run_until_idle()
    candidate = _candidate(graph, graph.objects(type="activity_evidence")[0])
    result = review_subject_fact_fn(graph, candidate.id, "confirm")
    runtime.run_until_idle()
    assert graph.get_object(result["verdict_id"]).data["status"] == "failed"
    assert not graph.objects(type="subject_fact")


def test_conflict_and_forget_are_append_only_lifecycles():
    graph, runtime = _runtime(); _evidence(graph); runtime.run_until_idle()
    evidence = graph.objects(type="activity_evidence")[0]
    first = _candidate(graph, evidence, "ActiveGraph")
    review_subject_fact_fn(graph, first.id, "confirm"); runtime.run_until_idle()
    second = _candidate(graph, evidence, "BabyAGI")
    review_subject_fact_fn(graph, second.id, "confirm"); runtime.run_until_idle()
    assert graph.objects(type="subject_contradiction")
    facts = graph.objects(type="subject_fact")
    current = [fact for fact in facts if fact.data["status"] == "promoted"][0]
    result = forget_subject_fact_fn(graph, current.id)
    tombstone = graph.get_object(result["tombstone_fact_id"])
    assert tombstone.data["status"] == "forgotten"
    assert graph.get_object(current.id).data["status"] == "superseded"
