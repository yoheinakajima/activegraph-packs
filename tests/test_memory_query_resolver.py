import hashlib

from activegraph import Graph, Runtime

from packs.activity_normalizer import pack as normalizer_pack
from packs.core import pack as core_pack
from packs.memory_gateway import MemoryGatewaySettings, pack as memory_pack
from packs.memory_gateway.tools import resolve_memory_query_fn
from packs.semantic_extraction import pack as semantic_pack


def test_evidence_floor_precedes_derived_memory_and_is_subject_scoped():
    graph = Graph(); runtime = Runtime(graph)
    runtime.load_pack(core_pack); runtime.load_pack(normalizer_pack)
    runtime.load_pack(semantic_pack)
    runtime.load_pack(memory_pack, settings=MemoryGatewaySettings(backend_url=":memory:"))
    text = "Yohei prefers small deterministic tools for ActiveGraph."
    digest = hashlib.sha256(text.encode()).hexdigest()
    item = graph.add_object("acquired_item", {"source_surface_id": "profile", "provider_item_id": "1", "dedup_key": "1", "source_ref": "profile:test", "source_hash": digest, "provider_time": None, "replay_mode": "inline", "replay_payload_ref": text, "replay_payload_hash": digest, "media_type": "text/plain", "importer_id": "test", "importer_version": "1"})
    graph.add_object("acquired_content", {"acquired_item_id": item.id, "normalized_content": text, "normalized_metadata": {"subject_scope": "owner_profile"}, "source_category": "local_knowledge", "connection_path": "pack", "is_fixture": True})
    runtime.run_until_idle()

    result = resolve_memory_query_fn(graph, "deterministic tools", subject_ref="owner")
    assert result["selected_tier"] in {"evidence", "annotated_evidence"}
    assert result["evidence_ids"]
    assert result["context_text"].startswith("[authoritative-evidence]")
    assert result["coverage"]["live_lookup_available"] is False

    other = resolve_memory_query_fn(graph, "deterministic tools", subject_ref="someone_else")
    assert other["evidence_ids"] == []
