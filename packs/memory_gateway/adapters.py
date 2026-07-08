"""External memory-service adapters for the Memory Gateway backend seam.

The contract is ``backend.MemoryBackend``; the base class is
``backend.ExternalMemoryBackend``. This module ships the first concrete
adapter — mem0 — both as a working integration and as the template for
writing others (Zep, Supermemory, pgvector, …): implement ``store_item`` and
``retrieve_by_query``, register a URL scheme, point ``backend_url`` at it.

Design rules for adapters:
  * Lazy imports only — the external SDK must never be a pack dependency.
  * Client injection — the constructor accepts a pre-built client so tests
    exercise the full mapping logic deterministically with a fake, and
    applications can pass a configured client (self-hosted, platform, …).
  * Same semantics — subject scoping and min_score filtering behave like the
    SQLite backend so behaviors upstream cannot tell the difference.
"""

from __future__ import annotations

from typing import Any, Optional

from .backend import ExternalMemoryBackend, register_backend


class Mem0Backend(ExternalMemoryBackend):
    """Memory backend backed by mem0 (https://github.com/mem0ai/mem0).

    Works with the OSS ``mem0.Memory``, the platform ``mem0.MemoryClient``,
    or any object exposing their shared surface::

        client.add(text, user_id=..., metadata=...)
        client.search(query, user_id=..., limit=...) -> {"results": [...]} | [...]

    Mapping:
      * ``subject_ref`` → mem0 ``user_id``. Subject-less ("global") memories
        are stored under ``global_user_id`` (default ``"global"``); a
        subject-scoped retrieval with ``include_global=True`` searches both
        and merges by score, mirroring the SQLite backend's semantics.
      * ``category`` / ``frame_id`` / ``item_id`` travel in mem0 metadata, so
        category filtering and same-frame exclusion keep working.
      * mem0's relevance score is clamped to [0, 1] and filtered by
        ``min_score`` — the shared threshold keeps one knob across backends.

    Retention, dedup, and retrieval stats are mem0's job (base-class no-ops).
    """

    def __init__(
        self,
        url: str = "mem0://default",
        client: Any = None,
        global_user_id: str = "global",
    ):
        super().__init__(url)
        self.global_user_id = global_user_id
        if client is None:
            try:
                from mem0 import Memory  # lazy: never a pack dependency
            except ImportError as exc:
                raise ImportError(
                    "Mem0Backend needs the 'mem0ai' package (pip install mem0ai), "
                    "or pass a pre-built client: Mem0Backend(url, client=...)"
                ) from exc
            client = Memory()
        self._client = client

    # -- writes ------------------------------------------------------------

    def store_item(self, item_id, text, category=None, confidence=0.7,
                   metadata=None, subject_ref=None) -> None:
        self._client.add(
            text,
            user_id=subject_ref or self.global_user_id,
            metadata={
                **(metadata or {}),
                "item_id": item_id,
                "category": category,
                "confidence": confidence,
            },
        )

    # -- retrieval ---------------------------------------------------------

    def retrieve_by_query(self, query, top_k=10, min_score=0.2, category=None,
                          subject_ref=None, subject_scoped=False,
                          include_global=True, exclude_frame_id=None) -> list[dict[str, Any]]:
        # Which user_ids to search: mirrors the SQLite subject-scoping matrix.
        if subject_scoped:
            user_ids = [subject_ref] if subject_ref else []
            if include_global:
                user_ids.append(self.global_user_id)
        else:
            # Unscoped recall searches the caller's scope plus global — mem0
            # has no true cross-user search, and unscoped callers in this
            # repo are single-user/demo paths where this is equivalent.
            user_ids = [subject_ref or self.global_user_id]
            if include_global and subject_ref:
                user_ids.append(self.global_user_id)

        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for user_id in user_ids:
            for entry in self._search(query, user_id, top_k):
                normalized = self._normalize(entry)
                if normalized is None:
                    continue
                if normalized["score"] < min_score:
                    continue
                meta = normalized.pop("_metadata")
                if category and meta.get("category") != category:
                    continue
                if exclude_frame_id and meta.get("frame_id") == exclude_frame_id:
                    continue
                if normalized["item_id"] in seen:
                    continue
                seen.add(normalized["item_id"])
                normalized["category"] = meta.get("category")
                normalized["confidence"] = meta.get("confidence", 0.7)
                results.append(normalized)

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    def _search(self, query: str, user_id: str, limit: int) -> list[dict]:
        try:
            raw = self._client.search(query, user_id=user_id, limit=limit)
        except TypeError:
            # Older client signatures lack ``limit``.
            raw = self._client.search(query, user_id=user_id)
        except Exception:
            return []  # recall degrades, never raises — same rule as embedders
        if isinstance(raw, dict):
            raw = raw.get("results", [])
        return raw if isinstance(raw, list) else []

    @staticmethod
    def _normalize(entry: dict) -> Optional[dict[str, Any]]:
        """Map a mem0 result entry onto the backend result shape."""
        if not isinstance(entry, dict):
            return None
        text = entry.get("memory") or entry.get("text") or ""
        if not text:
            return None
        metadata = entry.get("metadata") or {}
        score = entry.get("score", 1.0)
        try:
            score = max(0.0, min(1.0, float(score)))
        except (TypeError, ValueError):
            score = 0.0
        return {
            "item_id": metadata.get("item_id") or entry.get("id") or "",
            "text": text,
            "score": round(score, 4),
            "_metadata": metadata,
        }


def register_mem0_backend(client: Any = None, global_user_id: str = "global") -> None:
    """Register the ``mem0://`` scheme so backend_url can select mem0.

    Call once at application startup; after that, set
    ``MemoryGatewaySettings.backend_url = "mem0://default"`` (and
    ``ChatSettings.memory_backend_url`` to the same value) and the whole
    memory lifecycle runs against mem0."""
    register_backend(
        "mem0", lambda url: Mem0Backend(url, client=client, global_user_id=global_user_id)
    )
