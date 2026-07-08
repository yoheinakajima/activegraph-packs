"""Regression suite for memory retrieval quality.

Built directly from the July 2026 agent-readiness report (§5.1), which
verified these exact failures against the then-current Jaccard-only scoring:
the stored memory "my favorite color is teal and I run a bakery called
Crumbtown" was missed by the queries "Quick check: what color do I like?",
"bakery", and "teal" at the default min_score of 0.2 — breaking the flagship
"personal agent remembers you" demo on natural phrasing.

The fix under test is hybrid scoring in the backend:
  * lexical_score = max(Jaccard overlap, query-term coverage) — coverage is
    immune to stored-sentence length, so short/keyword queries recall.
  * with an embedder, item score = max(cosine, lexical) — "a memory is as
    relevant as its strongest signal": embeddings add rephrasing recall
    without ever costing exact-keyword recall.

Everything here is deterministic: no API key, no network (HashEmbedder).
"""

from __future__ import annotations

import pytest

from packs.memory_gateway.backend import (
    SqliteMemoryBackend,
    clear_embedder,
    lexical_score,
    set_embedder,
    set_embedder_factory,
)
from packs.memory_gateway.embedders import HashEmbedder

# The exact stored sentence and default threshold from the report.
REPORT_MEMORY = "my favorite color is teal and I run a bakery called Crumbtown"
DEFAULT_MIN_SCORE = 0.2  # MemoryGatewaySettings.min_retrieval_score default

# (query, why it previously failed)
REPORT_FAILURES = [
    ("Quick check: what color do I like?", "rephrased natural question"),
    ("bakery", "single keyword vs long sentence"),
    ("teal", "single keyword vs long sentence"),
]

REPORT_PASSES = [  # previously passing — must keep passing
    "What is my favorite color and what business do I run?",
    "favorite color",
]


@pytest.fixture(autouse=True)
def _lexical_default():
    """Every test starts and ends with no embedder registered."""
    clear_embedder()
    set_embedder_factory(None)
    yield
    clear_embedder()
    set_embedder_factory(None)


def _backend_with_report_memory() -> SqliteMemoryBackend:
    backend = SqliteMemoryBackend(":memory:")
    backend.store_item("m_report", REPORT_MEMORY, category="preference",
                       subject_ref="user:eval")
    return backend


# ---------------------------------------------------------------- lexical mode


@pytest.mark.parametrize("query,reason", REPORT_FAILURES)
def test_report_failure_cases_now_recall(query, reason):
    """Every verified §5.1 miss must hit at the default threshold."""
    backend = _backend_with_report_memory()
    results = backend.retrieve_by_query(query, top_k=5, min_score=DEFAULT_MIN_SCORE)
    ids = [r["item_id"] for r in results]
    assert "m_report" in ids, (
        f"query {query!r} ({reason}) missed the stored memory again — "
        f"§5.1 regression. Results: {results}"
    )


@pytest.mark.parametrize("query", REPORT_PASSES)
def test_report_passing_cases_still_recall(query):
    """Queries that hit under Jaccard-only scoring must keep hitting."""
    backend = _backend_with_report_memory()
    results = backend.retrieve_by_query(query, top_k=5, min_score=DEFAULT_MIN_SCORE)
    assert any(r["item_id"] == "m_report" for r in results)


def test_unrelated_memory_stays_below_threshold():
    """Coverage scoring must not turn recall into 'return everything'."""
    backend = _backend_with_report_memory()
    backend.store_item("m_noise", "the quarterly report is due next friday",
                       category="fact", subject_ref="user:eval")
    for query, _ in REPORT_FAILURES:
        results = backend.retrieve_by_query(query, top_k=5, min_score=DEFAULT_MIN_SCORE)
        assert all(r["item_id"] != "m_noise" for r in results), (
            f"unrelated memory leaked into results for {query!r}"
        )


def test_coverage_ranks_fuller_matches_higher():
    """An item matching more of the query outranks a partial match."""
    backend = SqliteMemoryBackend(":memory:")
    backend.store_item("m_full", "favorite color is teal")
    backend.store_item("m_partial", "teal is a shade the bathroom is painted in")
    results = backend.retrieve_by_query("favorite color teal", top_k=5, min_score=0.0)
    assert results[0]["item_id"] == "m_full"


def test_lexical_score_shape():
    """Unit checks on the scoring function itself."""
    # Single query term literally present → full coverage.
    assert lexical_score("teal", REPORT_MEMORY) == 1.0
    # Interrogative words don't dilute coverage.
    assert lexical_score("what color do I like?", REPORT_MEMORY) >= 0.2
    # Near-identical sentences keep a high score via Jaccard.
    assert lexical_score(REPORT_MEMORY, REPORT_MEMORY) == 1.0
    # No content overlap → zero.
    assert lexical_score("deploy the staging server", REPORT_MEMORY) == 0.0
    # Degenerate inputs never error.
    assert lexical_score("", REPORT_MEMORY) == 0.0
    assert lexical_score("the a an", REPORT_MEMORY) == 0.0


# ---------------------------------------------------------------- hybrid mode


class SynonymEmbedder(HashEmbedder):
    """HashEmbedder plus a tiny synonym fold, so a rephrased query lands on
    the same buckets as the stored sentence — a deterministic stand-in for
    what a real semantic embedder does with paraphrases. Implements the
    runtime EmbeddingProvider signature, like its base."""

    SYNONYMS = {"shop": "bakery", "business": "bakery", "hue": "color"}

    def embed(self, *, texts, model=""):
        folded = []
        for text in texts:
            words = [self.SYNONYMS.get(w, w) for w in text.lower().split()]
            folded.append(" ".join(words))
        return super().embed(texts=folded, model=model)


def test_embedding_recovers_rephrasing_lexical_misses():
    """A paraphrase sharing no content words recalls through the vector path."""
    set_embedder(SynonymEmbedder())
    backend = SqliteMemoryBackend(":memory:")
    backend.store_item("m1", "bakery color")
    # Every content word in the query is a synonym of the stored words, so
    # the lexical signal is exactly zero — only the vector path can recall.
    assert lexical_score("hue shop", "bakery color") == 0.0
    results = backend.retrieve_by_query("hue shop", top_k=5, min_score=DEFAULT_MIN_SCORE)
    assert any(r["item_id"] == "m1" for r in results), (
        "synonym paraphrase should recall via the embedding signal"
    )


def test_keyword_recall_survives_embedder():
    """max(cosine, lexical): enabling embeddings must never lose an
    exact-keyword hit, even when the vectors disagree."""

    class OrthogonalEmbedder:
        """Pathological embedder: every text gets a distinct one-hot vector,
        so cosine between any query and any item is 0."""

        def __init__(self):
            self._seen: dict[str, int] = {}

        def embed(self, texts):
            out = []
            for t in texts:
                idx = self._seen.setdefault(t, len(self._seen))
                vec = [0.0] * 64
                vec[idx % 64] = 1.0
                out.append(vec)
            return out

    set_embedder(OrthogonalEmbedder())
    backend = _backend_with_report_memory()
    results = backend.retrieve_by_query("teal", top_k=5, min_score=DEFAULT_MIN_SCORE)
    assert any(r["item_id"] == "m_report" for r in results), (
        "lexical signal must survive a disagreeing embedder (max, not replace)"
    )


def test_hybrid_takes_strongest_signal():
    """Item score equals the max of the lexical and cosine signals."""
    set_embedder(HashEmbedder())
    backend = SqliteMemoryBackend(":memory:")
    backend.store_item("m1", "favorite color teal")
    results = backend.retrieve_by_query("teal", top_k=5, min_score=0.0)
    (hit,) = [r for r in results if r["item_id"] == "m1"]
    lex = lexical_score("teal", "favorite color teal")
    assert hit["score"] >= round(lex, 4), "hybrid score must never undercut lexical"
