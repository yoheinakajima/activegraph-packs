"""Unit tests for the memory_gateway embedding seam.

These exercise the pluggable embedding path directly (no subprocess) to prove:
  1. With a registered embedder, items are embedded at write time and recall
     ranks by cosine similarity (vector path).
  2. Items written WITHOUT an embedder (NULL vector) still recall via the
     lexical fallback even after an embedder is later registered.
  3. The system never raises without a key: a missing embedder, a misbehaving
     embedder, and a no-key auto_configure all degrade to lexical.

The embedder here is a tiny deterministic fake — no API key, no network — which
is exactly the point: the seam is provider-agnostic.
"""

from __future__ import annotations

import math

import pytest

from packs.memory_gateway.backend import (
    SqliteMemoryBackend,
    auto_configure_embedder,
    clear_embedder,
    get_embedder,
    set_embedder,
    set_embedder_factory,
)


class FakeEmbedder:
    """Deterministic bag-of-words embedder over a tiny fixed vocabulary.

    Each text maps to a vector of per-word counts, so texts sharing words have a
    high cosine similarity. No randomness, no external calls."""

    VOCAB = ["dark", "mode", "light", "coffee", "tea", "prefer", "morning"]

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            words = text.lower().split()
            vectors.append([float(words.count(term)) for term in self.VOCAB])
        return vectors


@pytest.fixture(autouse=True)
def _reset_embedder():
    """Ensure every test starts and ends on the lexical default."""
    clear_embedder()
    set_embedder_factory(None)
    yield
    clear_embedder()
    set_embedder_factory(None)


def test_vector_path_ranks_by_cosine():
    """With an embedder, recall ranks semantically-closest item first."""
    set_embedder(FakeEmbedder())
    backend = SqliteMemoryBackend(":memory:")

    backend.store_item("m1", "dark mode prefer", category="preference")
    backend.store_item("m2", "coffee morning", category="preference")

    results = backend.retrieve_by_query("dark mode", top_k=5, min_score=0.0)
    assert results, "embedder path should return ranked results"
    assert results[0]["item_id"] == "m1", (
        f"closest match should rank first, got {[r['item_id'] for r in results]}"
    )
    # The unrelated item should score strictly lower than the match.
    by_id = {r["item_id"]: r["score"] for r in results}
    assert by_id["m1"] > by_id.get("m2", 0.0)


def test_items_are_embedded_at_write_time():
    """store_item persists a vector when an embedder is active."""
    set_embedder(FakeEmbedder())
    backend = SqliteMemoryBackend(":memory:")
    backend.store_item("m1", "dark mode", category="preference")

    row = backend._conn.execute(
        "SELECT embedding FROM memory_items WHERE item_id = ?", ("m1",)
    ).fetchone()
    assert row is not None and row[0] is not None, "vector should be persisted"


def test_lexical_fallback_for_unembedded_items():
    """Items stored without a vector still recall lexically.

    Mixed stores (some rows have vectors, some don't) must not break recall:
    the backend scores per-item, falling back to lexical for NULL embeddings."""
    backend = SqliteMemoryBackend(":memory:")
    # Stored with NO embedder → embedding column is NULL.
    backend.store_item("m1", "dark mode preference", category="preference")

    # Register an embedder AFTER the write; m1 has no vector.
    set_embedder(FakeEmbedder())
    results = backend.retrieve_by_query("dark mode", top_k=5, min_score=0.0)
    ids = [r["item_id"] for r in results]
    assert "m1" in ids, f"unembedded item should still recall lexically, got {ids}"


def test_no_embedder_is_pure_lexical():
    """Default (no embedder) recall is lexical and never raises."""
    assert get_embedder() is None
    backend = SqliteMemoryBackend(":memory:")
    backend.store_item("m1", "dark mode preference", category="preference")

    results = backend.retrieve_by_query("dark mode", top_k=5, min_score=0.1)
    assert any(r["item_id"] == "m1" for r in results)


def test_misbehaving_embedder_degrades_to_lexical():
    """An embedder that raises must not break writes or recall."""

    class BrokenEmbedder:
        def embed(self, texts):
            raise RuntimeError("provider exploded")

    set_embedder(BrokenEmbedder())
    backend = SqliteMemoryBackend(":memory:")
    # Should not raise even though the embedder throws.
    backend.store_item("m1", "dark mode preference", category="preference")

    row = backend._conn.execute(
        "SELECT embedding FROM memory_items WHERE item_id = ?", ("m1",)
    ).fetchone()
    assert row[0] is None, "failed embedding should persist NULL, not raise"

    results = backend.retrieve_by_query("dark mode", top_k=5, min_score=0.1)
    assert any(r["item_id"] == "m1" for r in results), "lexical fallback on error"


def test_auto_configure_without_key_stays_lexical():
    """auto_configure_embedder with no factory / no key returns None, stays lexical."""
    assert auto_configure_embedder() is None
    assert get_embedder() is None

    # A factory that finds no key returns None → still lexical, no error.
    set_embedder_factory(lambda: None)
    assert auto_configure_embedder() is None
    assert get_embedder() is None

    # A factory that raises must be swallowed → still lexical.
    def _boom():
        raise RuntimeError("no key")

    set_embedder_factory(_boom)
    assert auto_configure_embedder() is None
    assert get_embedder() is None


def test_auto_configure_uses_factory_when_available():
    """When a factory yields an embedder, auto_configure activates it."""
    set_embedder_factory(lambda: FakeEmbedder())
    emb = auto_configure_embedder()
    assert emb is not None
    assert get_embedder() is emb


# ------------------------------------------- runtime EmbeddingProvider seam


def test_pack_embedders_satisfy_runtime_protocol():
    """Seam adoption proof (activegraph >=1.3): this pack's embedders ARE
    runtime EmbeddingProviders, and the runtime's own test double drops
    straight into the pack's set_embedder seam."""
    from activegraph.llm.embedding import EmbeddingProvider, HashEmbeddingProvider

    from packs.memory_gateway.embedders import HashEmbedder

    assert isinstance(HashEmbedder(), EmbeddingProvider)
    vectors = HashEmbedder().embed(texts=["a b", "c"], model="")
    assert len(vectors) == 2

    # The runtime's provider, unmodified, behind the pack seam:
    set_embedder(HashEmbeddingProvider())
    backend = SqliteMemoryBackend(":memory:")
    backend.store_item("m1", "dark mode preference", category="preference")
    results = backend.retrieve_by_query("dark mode", top_k=5, min_score=0.0)
    assert any(r["item_id"] == "m1" for r in results)


# ---------------------------------------------------------------- P10:
# first-party embedding rides the runtime's RECORDED path (ctx.embed).


class CountingProvider:
    """Runtime EmbeddingProvider double: deterministic vectors, call count."""

    default_model = "fixture-embed-1"

    def __init__(self):
        self.calls = 0

    def embed(self, *, texts: list[str], model: str) -> list[list[float]]:
        self.calls += 1
        return [
            [float(len(t)), float(sum(map(ord, t)) % 97), 1.0] for t in texts
        ]


class PoisonProvider:
    """Raises on any embed call — proves zero external contact on replay."""

    default_model = "fixture-embed-1"

    def embed(self, *, texts: list[str], model: str) -> list[list[float]]:
        raise AssertionError(
            "external embedding contact during replay — the recorded "
            "cache should have served this request"
        )


def _memory_runtime(provider, persist_to=None, load_from=None):
    from activegraph import Graph, Runtime
    from packs.core import pack as core_pack
    from packs.memory_gateway import pack as mg_pack, MemoryGatewaySettings

    settings = MemoryGatewaySettings(
        acceptance_threshold=0.6,
        auto_accept_categories=["preference"],
    )
    if load_from is not None:
        rt = Runtime.load(
            load_from,
            embedding_provider=provider,
            replay_embedding_cache=True,
        )
    elif persist_to is not None:
        rt = Runtime(Graph(), persist_to=persist_to, embedding_provider=provider)
    else:
        rt = Runtime(Graph(), embedding_provider=provider)
    rt.load_pack(core_pack)
    rt.load_pack(mg_pack, settings=settings)
    return rt


def _store_one_memory(rt, text: str) -> None:
    rt.graph.add_object("memory_candidate", {
        "text": text,
        "confidence": 0.85,
        "source_ids": [],
        "observation_ids": [],
        "category": "preference",
        "subject_ref": None,
        "accepted": False,
        "evaluation_id": None,
        "frame_id": "frame_p10",
    })
    rt.run_until_idle()


def _retrieve(rt, query: str) -> list[str]:
    req = rt.graph.add_object("memory_retrieval_request", {
        "query": query,
        "top_k": 5,
        "min_score": 0.0,
        "behavior_name": "p10_fixture",
    })
    rt.run_until_idle()
    retrievals = [
        o for o in rt.graph.objects(type="memory_retrieval")
        if o.data.get("request_id") == req.id
    ]
    assert retrievals, "memory_retriever did not fulfill the request"
    return list(retrievals[-1].data.get("item_ids") or [])


def test_p10_first_party_embedding_is_runtime_recorded(tmp_path):
    """Write-time and query-time embeddings emit the runtime's
    embedding.requested/responded event pairs, and the direct pack-level
    embedder is NOT used when the runtime has a provider."""
    from packs.memory_gateway.backend import clear_all_backends

    clear_all_backends()
    legacy = FakeEmbedder()
    legacy.calls = 0
    real_embed = legacy.embed

    def counting_legacy(texts):
        legacy.calls += 1
        return real_embed(texts)

    legacy.embed = counting_legacy
    set_embedder(legacy)  # present, but must stay unused (P10)

    provider = CountingProvider()
    rt = _memory_runtime(provider, persist_to=str(tmp_path / "p10.db"))
    _store_one_memory(rt, "the user prefers dark mode everywhere")
    item_ids = _retrieve(rt, "the user prefers dark mode everywhere")

    assert item_ids, "stored memory should be recalled"
    assert provider.calls == 2  # one embed at write time, one per query
    assert legacy.calls == 0, (
        "first-party pack code must not call the direct embedder when the "
        "runtime records embeddings (P10)"
    )
    requested = [e for e in rt.graph.events if e.type == "embedding.requested"]
    responded = [e for e in rt.graph.events if e.type == "embedding.responded"]
    assert len(requested) == 2 and len(responded) == 2
    assert all(e.payload.get("cache_hit") is False for e in requested)


def test_p10_memory_round_trip_replays_with_zero_external_contact(tmp_path):
    """The P10 acceptance fixture: a recorded memory-gateway embedding
    round-trip replays from the log — the SAME retrieval re-runs against a
    provider that raises on any call, succeeds, and reports a cache hit."""
    from packs.memory_gateway.backend import clear_all_backends

    clear_all_backends()
    db = str(tmp_path / "p10_replay.db")
    text = "the user prefers dark mode everywhere"

    live_provider = CountingProvider()
    live = _memory_runtime(live_provider, persist_to=db)
    _store_one_memory(live, text)
    live_items = _retrieve(live, text)
    assert live_items and live_provider.calls == 2

    # Reload the run from its log. The poison provider proves that the
    # replayed embedding comes from the recorded events, not the network.
    replay = _memory_runtime(PoisonProvider(), load_from=db)
    replay_items = _retrieve(replay, text)

    assert replay_items == live_items
    new_requests = [
        e for e in replay.graph.events
        if e.type == "embedding.requested"
        and e.payload.get("cache_hit") is True
    ]
    assert new_requests, "replayed retrieval should hit the recorded cache"


def test_p10_without_runtime_provider_legacy_embedder_still_works(tmp_path):
    """Back-compat: no embedding_provider on the Runtime → the bound
    recorded path is skipped and the process-global embedder behaves
    exactly as before (no events, vectors still computed)."""
    from packs.memory_gateway.backend import clear_all_backends

    clear_all_backends()
    legacy = FakeEmbedder()
    set_embedder(legacy)
    rt = _memory_runtime(None)
    _store_one_memory(rt, "the user prefers dark mode everywhere")
    item_ids = _retrieve(rt, "dark mode")
    assert item_ids
    assert not [
        e for e in rt.graph.events if e.type.startswith("embedding.")
    ]
