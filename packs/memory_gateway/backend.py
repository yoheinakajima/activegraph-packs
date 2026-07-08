"""Memory Gateway backend — SQLite implementation with a pluggable
embedding seam.

This is the default backend for Memory Gateway Pack. It provides a simple
store for MemoryItems backed by SQLite, with two retrieval modes:

  * Lexical (default)  — Jaccard keyword overlap. Zero dependencies, never
    errors, works out of the box with no API key.
  * Embedding (opt-in) — cosine similarity over vectors. Activated *only*
    when an embedder is registered via ``set_embedder`` (or discovered by
    ``auto_configure_embedder``). With no embedder the backend stays fully
    lexical and never raises.

The selection is automatic: ``store_item`` embeds an item iff an embedder is
present, and ``retrieve_by_query`` ranks by cosine iff an embedder is present
(falling back to lexical for any item without a stored vector, and on any
embedder error). This is the "trivially pluggable embeddings" seam — see
``docs/long-term-memory.md`` for wiring an OpenAI/other provider. We never
bundle a provider as a hard dependency.

The backend interface is minimal:
  store_item(item_id, text, category, confidence, metadata)
  retrieve_by_query(query, top_k, min_score, category) → list[dict]
  find_by_text(text) → Optional[str]   # write-path dedup
  enforce_limit(max_items)
  clear()

External backends (Postgres+pgvector, Mem0, Supermemory, …) implement this
same interface and are swapped in by pointing ``backend_url`` at a custom
factory; the docs page covers the integration boundary.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from typing import Any, Callable, Optional, Protocol, runtime_checkable


# ------------------------------------------------------------------ helpers


def _word_set(text: str) -> set[str]:
    """Lowercase word set for keyword search."""
    STOPWORDS = {
        "a", "an", "the", "and", "or", "in", "on", "at", "to",
        "for", "of", "with", "is", "are", "was", "were", "be",
        "it", "i", "we", "you", "they", "not",
    }
    words = re.findall(r"[a-z]+", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def _jaccard(a: str, b: str) -> float:
    wa, wb = _word_set(a), _word_set(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


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


def _safe_embed(texts: list[str]) -> Optional[list[list[float]]]:
    """Embed texts with the active embedder, swallowing any failure → None.

    Guarantees the lexical path is always reachable: a misbehaving or
    unconfigured embedder degrades to lexical instead of raising."""
    emb = get_embedder()
    if emb is None:
        return None
    try:
        vectors = emb.embed(texts)
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
                subject_ref TEXT
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

        Ranking mode is selected automatically: cosine similarity over stored
        vectors when an embedder is registered, lexical Jaccard otherwise. The
        two modes are kept on the same [0,1] scale so a single min_score works
        for both. Lexical is the per-item fallback for any item lacking a vector
        and for the whole query if embedding the query fails — recall never
        errors just because embeddings are misconfigured.

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
        sql = "SELECT item_id, text, category, confidence, embedding, metadata FROM memory_items"
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
            item_id, text, cat, conf, embedding_json, metadata_json = row
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
            score = None
            if query_vec is not None and embedding_json:
                try:
                    score = _cosine(query_vec, json.loads(embedding_json))
                except Exception:
                    score = None
            if score is None:  # lexical fallback (no vector / embed failed)
                score = _jaccard(query, text)
            if score >= min_score:
                scored.append({
                    "item_id": item_id,
                    "text": text,
                    "category": cat,
                    "confidence": conf,
                    "score": round(score, 4),
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

_backends: dict[str, SqliteMemoryBackend] = {}


def get_backend(db_url: str = ":memory:") -> SqliteMemoryBackend:
    """Get or create a backend instance for the given db_url.

    In-memory backends are per-process singletons (shared within a run).
    File-based backends are also singletons keyed by path.
    """
    if db_url not in _backends:
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
