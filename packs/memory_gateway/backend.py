"""Memory Gateway backend — SQLite implementation with a pluggable
embedding seam.

This is the default backend for Memory Gateway Pack. It provides a simple
store for MemoryItems backed by SQLite, with hybrid retrieval scoring:

  * Lexical (always on)   — max(Jaccard overlap, query-term coverage). Zero
    dependencies, never errors, works out of the box with no API key, and
    robust to the short-query-vs-long-sentence shape ("teal" finds "my
    favorite color is teal…"). See ``lexical_score``.
  * Embedding (opt-in)    — cosine similarity over vectors, activated when an
    embedder is registered via ``set_embedder`` (or discovered by
    ``auto_configure_embedder``; ``embedders.default_embedder_factory`` wires
    OpenAI from the environment). Cosine is BLENDED with the lexical signal —
    an item's score is the max of the two — so embeddings add rephrasing
    recall without ever costing exact-keyword recall.

``store_item`` embeds an item iff an embedder is present; items without a
stored vector (or on any embedder error) simply score lexically. We never
bundle an embedding provider as a hard dependency.

External backends (mem0, Zep, Supermemory, Postgres+pgvector, …) implement
the ``MemoryBackend`` protocol — usually by subclassing
``ExternalMemoryBackend`` and implementing just ``store_item`` +
``retrieve_by_query`` — and are switched in by registering a URL scheme with
``register_backend`` and pointing ``backend_url`` at it (e.g.
``mem0://default``). ``adapters.py`` ships a mem0 adapter; the docs page
covers the integration boundary.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator, Optional, Protocol, runtime_checkable


# ------------------------------------------------------------------ helpers


STOPWORDS = {
    # articles / conjunctions / prepositions / auxiliaries
    "a", "an", "the", "and", "or", "in", "on", "at", "to",
    "for", "of", "with", "is", "are", "was", "were", "be",
    "it", "i", "we", "you", "they", "not",
    # interrogatives and question auxiliaries: they carry no memory content,
    # and leaving them in dilutes coverage scoring for natural questions
    # ("what color do I like?" should reduce to {color, like}).
    "what", "whats", "who", "whom", "whose", "when", "where", "which",
    "why", "how", "does", "did", "will", "would", "can", "could",
    "should", "shall", "please",
}


def _word_set(text: str) -> set[str]:
    """Lowercase word set for keyword search."""
    words = re.findall(r"[a-z]+", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def _jaccard(a: str, b: str) -> float:
    wa, wb = _word_set(a), _word_set(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def lexical_score(query: str, text: str) -> float:
    """Lexical relevance of *text* to *query*, in [0, 1].

    max(Jaccard overlap, query-term coverage). Jaccard alone is brittle for
    the common recall shape — a short query against a long stored sentence —
    because the union term punishes every stored word the query didn't say:
    query "teal" against "my favorite color is teal and I run a bakery called
    Crumbtown" gets Jaccard ~0.14 and misses a 0.2 threshold even though the
    query term is literally present. Coverage (|query ∩ text| / |query|) is
    the asymmetric complement: it asks "how much of what was ASKED does this
    memory contain?" and is immune to stored-sentence length. Taking the max
    keeps near-identical sentences at Jaccard ~1.0 while making keyword and
    natural-question recall behave.
    """
    qwords = _word_set(query)
    twords = _word_set(text)
    if not qwords or not twords:
        return 0.0
    overlap = len(qwords & twords)
    coverage = overlap / len(qwords)
    jaccard = overlap / len(qwords | twords)
    return max(jaccard, coverage)


def _normalize_text(text: str) -> str:
    """Normalize memory text for dedup: lowercase, collapse whitespace, strip
    trailing punctuation. Two statements that differ only in casing/spacing/
    final punctuation are treated as the same memory."""
    return re.sub(r"\s+", " ", text.strip().lower()).rstrip(".!?,;: ")


# ------------------------------------------------------------------ embedding seam
#
# The embedding seam is the single switch between lexical (default) and
# embedding-based recall. Keep this dependency-free: we define only the
# *protocol* and a process-global registry. A real provider (OpenAI, Cohere,
# a local sentence-transformer, …) is plugged in by the application — never
# bundled here — so the library stays installable and testable with no API key.


@runtime_checkable
class Embedder(Protocol):
    """Anything that turns text into vectors. The one method the backend needs.

    Implementations live in the *application*, not in this pack. Example::

        class OpenAIEmbedder:
            def embed(self, texts: list[str]) -> list[list[float]]:
                # call your provider, return one vector per input text
                ...

        from packs.memory_gateway.backend import set_embedder
        set_embedder(OpenAIEmbedder())   # now recall is embedding-based
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


_embedder: Optional[Embedder] = None
# Optional factory an application can register so auto_configure_embedder() can
# lazily build an embedder when a key is present. Left None by default → lexical.
_embedder_factory: Optional[Callable[[], Optional[Embedder]]] = None


def set_embedder(embedder: Optional[Embedder]) -> None:
    """Register (or clear, with None) the active embedder.

    Once set, store_item embeds new items and retrieve_by_query ranks by cosine
    automatically. Pass None to fall back to lexical. This is the whole switch."""
    global _embedder
    _embedder = embedder


def get_embedder() -> Optional[Embedder]:
    """Return the active embedder, or None when recall is lexical."""
    return _embedder


def clear_embedder() -> None:
    """Reset to the lexical default. Mainly for tests."""
    global _embedder
    _embedder = None


def set_embedder_factory(factory: Optional[Callable[[], Optional[Embedder]]]) -> None:
    """Register a zero-arg factory used by auto_configure_embedder().

    The factory should return an Embedder when the environment is configured
    (e.g. an API key is present) or None otherwise. It must not raise."""
    global _embedder_factory
    _embedder_factory = factory


def auto_configure_embedder() -> Optional[Embedder]:
    """Best-effort, never-raises switch to embedding-based recall.

    Call this once at startup. If an embedder is already set, it wins. Otherwise
    we try the registered factory (if any). With no factory / no key / any error
    we stay lexical and return None — the system must never error without a key.

    Applications wire real auto-detection by calling set_embedder_factory(...)
    with a factory that checks for their provider key. We deliberately do NOT
    import any provider here so the pack has zero embedding dependencies."""
    if _embedder is not None:
        return _embedder
    if _embedder_factory is None:
        return None
    try:
        emb = _embedder_factory()
    except Exception:
        return None
    if emb is not None:
        set_embedder(emb)
    return emb


def _invoke_embedder(emb: Any, texts: list[str]) -> list[list[float]]:
    """Call an embedder in whichever shape it implements.

    Preferred: the runtime's EmbeddingProvider protocol
    (``embed(*, texts, model)`` + ``default_model``, activegraph >=1.3).
    Legacy: this pack's original ``embed(texts)`` seam, kept working
    because it is public API that third-party embedders may implement."""
    try:
        return emb.embed(texts=texts, model=getattr(emb, "default_model", "") or "")
    except TypeError:
        return emb.embed(texts)


# ------------------------------------------------------------------ recorded path (P10)
#
# The runtime's Context.embed / Runtime.embed is the RECORDED embedding
# path: every call emits embedding.requested/embedding.responded events
# and replays from the log with zero provider contact (runtime CONTRACT
# v1.8 #6). First-party pack code reaches it by binding the current
# behavior ctx (or a Runtime) around backend calls — see
# runtime_recorded_embedding. When a recorded path is bound, _safe_embed
# prefers it over the process-global embedder; a bound-but-failing
# recorded call degrades to lexical (None), NEVER silently falls back to
# the unrecorded direct embedder — falling back would reintroduce
# unrecorded external I/O, which is exactly what P10 removes. Direct
# provider calls (set_embedder without a runtime) remain possible for
# third-party embedders and bare-graph hosts, but first-party packs no
# longer use them when a runtime is present.

_RECORDED_EMBED: ContextVar[Optional[Callable[[list[str]], list[list[float]]]]] = (
    ContextVar("memory_gateway_recorded_embed", default=None)
)


def _recorded_embed_fn(handle: Any) -> Optional[Callable[[list[str]], list[list[float]]]]:
    """A ``texts → vectors`` closure over *handle*'s recorded embed path.

    *handle* is a behavior ``ctx`` (activegraph Context) or a ``Runtime``.
    Returns None when the handle cannot serve recorded embeddings — no
    ``.embed``, or no ``embedding_provider`` on the (ctx's) runtime to
    resolve the default model — so callers fall through to the legacy
    process-global embedder unchanged."""
    if handle is None:
        return None
    embed = getattr(handle, "embed", None)
    if embed is None:
        return None
    runtime = getattr(handle, "_runtime", None) or handle
    if getattr(runtime, "embedding_provider", None) is None:
        return None

    def _embed(texts: list[str]) -> list[list[float]]:
        result: list[list[float]] = embed(texts)
        return result

    return _embed


@contextmanager
def runtime_recorded_embedding(handle: Any) -> Iterator[bool]:
    """Prefer *handle*'s recorded embed path inside the ``with`` block.

    ``handle`` is a behavior ``ctx`` or a ``Runtime``. Yields True when
    the recorded path is bound (embedding.requested/responded events and
    replay apply to every embed the block performs); False when the
    handle cannot serve embeddings, in which case nothing changes and
    the legacy embedder keeps working exactly as before. Bindings nest
    and are task-local (ContextVar), so concurrent behaviors cannot see
    one another's runtime."""
    embed_fn = _recorded_embed_fn(handle)
    if embed_fn is None:
        yield False
        return
    token = _RECORDED_EMBED.set(embed_fn)
    try:
        yield True
    finally:
        _RECORDED_EMBED.reset(token)


def _safe_embed(texts: list[str]) -> Optional[list[list[float]]]:
    """Embed texts, swallowing any failure → None (lexical fallback).

    Prefers a bound recorded runtime path (see
    ``runtime_recorded_embedding``); otherwise uses the process-global
    embedder. Guarantees the lexical path is always reachable: a
    misbehaving or unconfigured embedder degrades to lexical instead of
    raising — and a failing RECORDED path degrades to lexical too,
    never to an unrecorded direct call."""
    recorded = _RECORDED_EMBED.get()
    if recorded is not None:
        try:
            vectors = recorded(texts)
        except Exception:
            return None
        if not vectors or len(vectors) != len(texts):
            return None
        return vectors
    emb = get_embedder()
    if emb is None:
        return None
    try:
        vectors = _invoke_embedder(emb, texts)
    except Exception:
        return None
    if not vectors or len(vectors) != len(texts):
        return None
    return vectors


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    # Clamp to [0,1] so scores compose with the lexical [0,1] range and the
    # shared min_score threshold. Negative cosines (opposite vectors) → 0.
    return max(0.0, min(1.0, dot / (na * nb)))


# ------------------------------------------------------------------ backend protocol
#
# Anything satisfying this protocol can be the memory store: behaviors and
# tools only ever talk to a backend through these methods. External services
# (mem0, Zep, Supermemory, Postgres+pgvector, …) plug in by subclassing
# ExternalMemoryBackend (which no-ops the SQLite-specific niceties) and
# registering a URL scheme with register_backend() — after which pointing
# MemoryGatewaySettings.backend_url at e.g. "mem0://default" switches the
# ENTIRE lifecycle (writer, retriever, chat recall) to the external store
# with zero behavior changes.


@runtime_checkable
class MemoryBackend(Protocol):
    """The full surface behaviors/tools use. Only ``store_item`` and
    ``retrieve_by_query`` carry the semantics; the rest are lifecycle and
    write-path helpers that external adapters may no-op (see
    ``ExternalMemoryBackend`` for safe defaults)."""

    def store_item(self, item_id: str, text: str, category: Optional[str] = None,
                   confidence: float = 0.7, metadata: Optional[dict] = None,
                   subject_ref: Optional[str] = None) -> None: ...

    def retrieve_by_query(self, query: str, top_k: int = 10, min_score: float = 0.2,
                          category: Optional[str] = None,
                          subject_ref: Optional[str] = None,
                          subject_scoped: bool = False, include_global: bool = True,
                          exclude_frame_id: Optional[str] = None) -> list[dict[str, Any]]: ...

    def find_by_text(self, text: str, subject_ref: Optional[str] = None) -> Optional[str]: ...
    def set_subject(self, item_id: str, subject_ref: Optional[str]) -> None: ...
    def get_subject(self, item_id: str) -> Optional[str]: ...
    def enforce_limit(self, max_items: int) -> None: ...
    def update_retrieval(self, item_id: str) -> None: ...
    def set_reliability(self, item_id: str, verdict: str, multiplier: float) -> None: ...
    def clear(self) -> None: ...
    def count(self) -> int: ...
    def close(self) -> None: ...


class ExternalMemoryBackend:
    """Convenience base for external-store adapters.

    Subclass and implement ``store_item`` + ``retrieve_by_query`` — the two
    methods with real semantics. Everything else defaults to a safe no-op
    chosen for external stores: no write-path text dedup (find_by_text →
    None means memory_writer always stores; external stores typically dedup
    server-side), no LRU eviction (retention is the store's job), no local
    retrieval stats. Override any of them when the service supports it.
    """

    def __init__(self, url: str):
        self.db_url = url
        self._reliability: dict[str, tuple[str, float]] = {}

    def store_item(self, item_id, text, category=None, confidence=0.7,
                   metadata=None, subject_ref=None) -> None:
        raise NotImplementedError

    def retrieve_by_query(self, query, top_k=10, min_score=0.2, category=None,
                          subject_ref=None, subject_scoped=False,
                          include_global=True, exclude_frame_id=None) -> list[dict[str, Any]]:
        raise NotImplementedError

    def find_by_text(self, text, subject_ref=None) -> Optional[str]:
        return None

    def set_subject(self, item_id, subject_ref) -> None:
        pass

    def get_subject(self, item_id) -> Optional[str]:
        return None

    def enforce_limit(self, max_items) -> None:
        pass

    def update_retrieval(self, item_id) -> None:
        pass

    def set_reliability(self, item_id, verdict, multiplier) -> None:
        """Store the shared reliability hook for adapters that apply it."""
        self._reliability[item_id] = (verdict, max(0.0, min(1.0, float(multiplier))))

    def apply_reliability(self, results, min_score=0.2, top_k=10):
        """Apply the common reversible reliability multiplier to adapter results."""
        adjusted = []
        for result in results:
            row = dict(result)
            verdict, multiplier = self._reliability.get(
                str(row.get("item_id") or ""), ("weak", 1.0)
            )
            raw_score = float(row.get("score", 0.0))
            row["raw_score"] = round(raw_score, 4)
            row["reliability_verdict"] = verdict
            row["reliability_multiplier"] = multiplier
            row["score"] = round(raw_score * multiplier, 4)
            if row["score"] >= min_score:
                adjusted.append(row)
        adjusted.sort(key=lambda item: item["score"], reverse=True)
        return adjusted[:top_k]

    def clear(self) -> None:
        pass

    def count(self) -> int:
        return 0

    def close(self) -> None:
        pass


# ------------------------------------------------------------------ backend


class SqliteMemoryBackend:
    """SQLite-backed memory store.

    Uses ':memory:' by default (no persistence across runs).
    Pass a file path for persistence: SqliteMemoryBackend('memory.db').
    """

    _instances: dict[str, "SqliteMemoryBackend"] = {}

    def __init__(self, db_url: str = ":memory:"):
        self.db_url = db_url
        self._conn = sqlite3.connect(db_url, check_same_thread=False)
        self._setup()

    def _setup(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_items (
                item_id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                category TEXT,
                confidence REAL DEFAULT 0.7,
                metadata TEXT DEFAULT '{}',
                created_at TEXT,
                last_retrieved_at TEXT,
                retrieval_count INTEGER DEFAULT 0,
                embedding TEXT,
                subject_ref TEXT,
                reliability_verdict TEXT DEFAULT 'weak',
                reliability_multiplier REAL DEFAULT 1.0
            )
        """)
        # Migration-safe: a DB file written by an older version may lack the
        # `embedding` and/or `subject_ref` columns. Add them lazily so persisted
        # stores keep working (and survive the restart cross-session fixtures
        # rely on). subject_ref scopes a memory to the user it is about, so
        # recall can isolate one user's memories from another's.
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(memory_items)")}
        if "embedding" not in cols:
            self._conn.execute("ALTER TABLE memory_items ADD COLUMN embedding TEXT")
        if "subject_ref" not in cols:
            self._conn.execute("ALTER TABLE memory_items ADD COLUMN subject_ref TEXT")
        if "reliability_verdict" not in cols:
            self._conn.execute(
                "ALTER TABLE memory_items ADD COLUMN reliability_verdict TEXT DEFAULT 'weak'"
            )
        if "reliability_multiplier" not in cols:
            self._conn.execute(
                "ALTER TABLE memory_items ADD COLUMN reliability_multiplier REAL DEFAULT 1.0"
            )
        self._conn.commit()

    def store_item(
        self,
        item_id: str,
        text: str,
        category: Optional[str] = None,
        confidence: float = 0.7,
        metadata: Optional[dict] = None,
        subject_ref: Optional[str] = None,
    ):
        """Store a new MemoryItem in the backend.

        If an embedder is registered, the item is embedded at write time and the
        vector persisted alongside it ("behavior-triggered embedding": this runs
        inside memory_writer). With no embedder the embedding column stays NULL
        and retrieval is purely lexical.

        ``subject_ref`` scopes the memory to the user it is about (NULL = a
        subject-less / global memory). retrieve_by_query uses it to isolate one
        user's memories from another's."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        vectors = _safe_embed([text])
        embedding_json = json.dumps(vectors[0]) if vectors else None

        self._conn.execute(
            """
            INSERT OR REPLACE INTO memory_items
                (item_id, text, category, confidence, metadata, created_at,
                 last_retrieved_at, retrieval_count, embedding, subject_ref)
            VALUES (?, ?, ?, ?, ?, ?, NULL, 0, ?, ?)
            """,
            (item_id, text, category, confidence, json.dumps(metadata or {}),
             now, embedding_json, subject_ref),
        )
        self._conn.commit()

    def find_by_text(
        self, text: str, subject_ref: Optional[str] = None
    ) -> Optional[str]:
        """Return the item_id of an existing memory whose normalized text matches
        *text* within the same subject scope, or None.

        Used by the write path to avoid storing the same statement twice when
        multiple proposers (Core + chat heuristic) fire on the same message.
        A stored item matches when its normalized text is equal AND it is in the
        same subject scope: same subject_ref, or EITHER side is NULL (so a
        subject-less Core candidate and a subject-scoped chat candidate for the
        same message still collapse, while two DIFFERENT users stating the same
        sentence stay separate)."""
        target = _normalize_text(text)
        if not target:
            return None
        for item_id, existing, existing_subj in self._conn.execute(
            "SELECT item_id, text, subject_ref FROM memory_items"
        ):
            if _normalize_text(existing) != target:
                continue
            if (
                existing_subj == subject_ref
                or existing_subj is None
                or subject_ref is None
            ):
                return item_id
        return None

    def set_subject(self, item_id: str, subject_ref: Optional[str]) -> None:
        """Set/upgrade an item's subject_ref.

        Used by the write path to promote a subject-less item to a scoped one
        once a later (subject-bearing) duplicate candidate for the same message
        is collapsed into it — so the final stored memory is correctly scoped
        regardless of which proposer fired first."""
        self._conn.execute(
            "UPDATE memory_items SET subject_ref = ? WHERE item_id = ?",
            (subject_ref, item_id),
        )
        self._conn.commit()

    def get_subject(self, item_id: str) -> Optional[str]:
        """Return the stored subject_ref for *item_id*, or None."""
        row = self._conn.execute(
            "SELECT subject_ref FROM memory_items WHERE item_id = ?", (item_id,)
        ).fetchone()
        return row[0] if row else None

    def retrieve_by_query(
        self,
        query: str,
        top_k: int = 10,
        min_score: float = 0.2,
        category: Optional[str] = None,
        subject_ref: Optional[str] = None,
        subject_scoped: bool = False,
        include_global: bool = True,
        exclude_frame_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Retrieve MemoryItems ranked by similarity to *query*.

        Scoring is hybrid: every item gets a lexical score (max of Jaccard
        overlap and query-term coverage — see ``lexical_score``), and when an
        embedder is registered AND the item has a stored vector, also a cosine
        score. The item's score is the MAX of the signals available for it —
        a memory is as relevant as its strongest signal. This is strictly
        never worse than either pure mode: embedding recall catches
        rephrasings that share no keywords, lexical recall catches exact
        keywords ("teal") whose cosine against a long sentence can be
        middling. Both signals live on the same [0,1] scale so a single
        min_score governs both. Recall never errors just because embeddings
        are misconfigured — a missing/failing embedder simply drops the
        cosine signal.

        Access control: when ``subject_scoped`` is True, only items whose
        subject_ref equals ``subject_ref`` are returned, so a caller acting for
        one user never sees another user's memories. ``include_global`` controls
        whether subject-less (NULL) memories — intended as shared/global facts —
        are ALSO returned: True (default) folds them in, False restricts recall
        strictly to the caller's own memories (the secure default for the chat
        read path, where untagged/legacy NULL rows would otherwise be readable by
        everyone). When ``subject_scoped`` is False, no subject filter is applied
        — for single-user/global callers and backward compatibility.

        Returns a list of dicts with: item_id, text, score, category, confidence.
        Sorted by score descending, limited to top_k.
        """
        where = []
        params: list[Any] = []
        if category:
            where.append("category = ?")
            params.append(category)
        if subject_scoped:
            # The caller's own memories always match. `subject_ref = NULL` is
            # never true in SQL, so an anonymous caller (subject_ref None) falls
            # through to the global clause below (or nothing, if globals excluded).
            if include_global:
                # NULL subject = global memory, visible to everyone.
                where.append("(subject_ref = ? OR subject_ref IS NULL)")
                params.append(subject_ref)
            else:
                # Strict isolation: only the caller's own (non-NULL) memories.
                where.append("subject_ref = ?")
                params.append(subject_ref)
        sql = (
            "SELECT item_id, text, category, confidence, embedding, metadata, "
            "reliability_verdict, reliability_multiplier FROM memory_items"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        cursor = self._conn.execute(sql, tuple(params))
        rows = cursor.fetchall()

        # Try to embed the query once; None → lexical for the whole query.
        query_vec = None
        q = _safe_embed([query])
        if q:
            query_vec = q[0]

        scored = []
        for row in rows:
            (
                item_id,
                text,
                cat,
                conf,
                embedding_json,
                metadata_json,
                reliability_verdict,
                reliability_multiplier,
            ) = row
            # Same-frame exclusion: a memory born in the frame that is asking
            # must not answer it. This turns what used to be a timing accident
            # (writes land in a later cascade than reads) into a designed
            # guarantee that survives any future behavior reordering.
            if exclude_frame_id:
                try:
                    if (json.loads(metadata_json or "{}").get("frame_id")
                            == exclude_frame_id):
                        continue
                except Exception:
                    pass
            score = lexical_score(query, text)
            if query_vec is not None and embedding_json:
                try:
                    score = max(score, _cosine(query_vec, json.loads(embedding_json)))
                except Exception:
                    pass  # bad stored vector → lexical signal already in place
            raw_score = score
            score = raw_score * float(reliability_multiplier or 0.0)
            if score >= min_score:
                scored.append({
                    "item_id": item_id,
                    "text": text,
                    "category": cat,
                    "confidence": conf,
                    "score": round(score, 4),
                    "raw_score": round(raw_score, 4),
                    "reliability_verdict": reliability_verdict or "weak",
                    "reliability_multiplier": float(reliability_multiplier or 0.0),
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def enforce_limit(self, max_items: int):
        """Evict least-recently-used items if over the limit."""
        cursor = self._conn.execute("SELECT COUNT(*) FROM memory_items")
        count = cursor.fetchone()[0]
        if count <= max_items:
            return
        excess = count - max_items
        self._conn.execute(
            """
            DELETE FROM memory_items WHERE item_id IN (
                SELECT item_id FROM memory_items
                ORDER BY COALESCE(last_retrieved_at, created_at) ASC
                LIMIT ?
            )
            """,
            (excess,),
        )
        self._conn.commit()

    def update_retrieval(self, item_id: str):
        """Update retrieval stats for an item."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            UPDATE memory_items
            SET retrieval_count = retrieval_count + 1,
                last_retrieved_at = ?
            WHERE item_id = ?
            """,
            (now, item_id),
        )
        self._conn.commit()

    def set_reliability(self, item_id: str, verdict: str, multiplier: float):
        """Set the outcome-derived retrieval multiplier for one memory item."""
        self._conn.execute(
            """
            UPDATE memory_items
            SET reliability_verdict = ?, reliability_multiplier = ?
            WHERE item_id = ?
            """,
            (verdict, max(0.0, min(1.0, float(multiplier))), item_id),
        )
        self._conn.commit()

    def clear(self):
        """Remove all stored items."""
        self._conn.execute("DELETE FROM memory_items")
        self._conn.commit()

    def count(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM memory_items")
        return cursor.fetchone()[0]

    def close(self) -> None:
        """Close the underlying SQLite connection so the file handle is
        released (required before deleting the DB file on some platforms)."""
        try:
            self._conn.close()
        except Exception:
            pass


# ------------------------------------------------------------------ factory

_backends: dict[str, MemoryBackend] = {}

# Registered external-backend factories, keyed by URL scheme ("mem0", "zep",
# …). A factory takes the full URL and returns a MemoryBackend. Registration
# is application-side (never automatic on import), same rule as embedders.
_backend_factories: dict[str, Callable[[str], MemoryBackend]] = {}


def register_backend(scheme: str, factory: Callable[[str], MemoryBackend]) -> None:
    """Register an external backend factory for a URL scheme.

    After ``register_backend("mem0", lambda url: Mem0Backend(url))``, any
    backend_url of the form ``mem0://...`` — in MemoryGatewaySettings,
    ChatSettings.memory_backend_url, or a retrieval request — resolves to the
    external store. One registration switches the whole memory lifecycle."""
    _backend_factories[scheme] = factory


def unregister_backend(scheme: str) -> None:
    """Remove a registered scheme (mainly for tests)."""
    _backend_factories.pop(scheme, None)


def get_backend(db_url: str = ":memory:") -> MemoryBackend:
    """Get or create a backend instance for the given db_url.

    Dispatch: a ``scheme://`` URL whose scheme was registered via
    register_backend() resolves through that factory; everything else
    (":memory:", file paths) is the built-in SQLite backend. Instances are
    per-process singletons keyed by URL, so the writer and every retrieval
    path share one store per URL.
    """
    if db_url not in _backends:
        scheme = db_url.split("://", 1)[0] if "://" in db_url else None
        if scheme and scheme in _backend_factories:
            _backends[db_url] = _backend_factories[scheme](db_url)
        else:
            _backends[db_url] = SqliteMemoryBackend(db_url)
    return _backends[db_url]


def clear_all_backends():
    """Clear all backend instances and release their SQLite connections.

    WARNING: this DELETES all stored rows. Use it to start a test from a clean
    slate, NOT to simulate a restart — for that use close_all_backends()."""
    for backend in _backends.values():
        backend.clear()
        backend.close()
    _backends.clear()


def close_all_backends():
    """Close connections and drop the in-process backend cache WITHOUT deleting
    any rows. The next get_backend() re-opens the same db_url from disk.

    This simulates a process restart: file-backed stores keep their data, which
    is exactly what the cross-session memory fixtures rely on to prove recall
    survives across sessions."""
    for backend in _backends.values():
        backend.close()
    _backends.clear()
