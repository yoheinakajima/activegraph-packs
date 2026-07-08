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
