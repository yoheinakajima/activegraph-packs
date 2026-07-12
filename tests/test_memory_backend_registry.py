"""Tests for the pluggable memory-backend seam.

Proves the integration boundary for external memory services (mem0, Zep, …):
  1. register_backend() routes scheme URLs to a custom factory; plain paths
     keep resolving to the built-in SQLite backend; instances are singletons.
  2. ExternalMemoryBackend defaults are safe no-ops, so an adapter only has
     to implement store_item + retrieve_by_query.
  3. The tools path (retrieve_memories_fn) works against a registered
     external backend unchanged — the switch is one settings value.
  4. Mem0Backend maps the backend contract onto the mem0 client surface
     correctly (subject_ref→user_id, metadata round-trip, min_score/category
     filtering, subject scoping, graceful degradation). Exercised against a
     deterministic fake client with the OSS mem0 add/search signature.
"""

from __future__ import annotations

import uuid

import pytest

from packs.memory_gateway.adapters import Mem0Backend, register_mem0_backend
from packs.memory_gateway.backend import (
    ExternalMemoryBackend,
    MemoryBackend,
    SqliteMemoryBackend,
    get_backend,
    register_backend,
    unregister_backend,
    _backends,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Isolate registry and singleton cache per test."""
    saved = dict(_backends)
    _backends.clear()
    yield
    for scheme in ("fake", "mem0"):
        unregister_backend(scheme)
    _backends.clear()
    _backends.update(saved)


class FakeBackend(ExternalMemoryBackend):
    """Minimal adapter: a dict store with substring retrieval."""

    def __init__(self, url):
        super().__init__(url)
        self.items: dict[str, dict] = {}

    def store_item(self, item_id, text, category=None, confidence=0.7,
                   metadata=None, subject_ref=None):
        self.items[item_id] = {"text": text, "category": category,
                               "confidence": confidence, "subject_ref": subject_ref}

    def retrieve_by_query(self, query, top_k=10, min_score=0.2, category=None,
                          subject_ref=None, subject_scoped=False,
                          include_global=True, exclude_frame_id=None):
        hits = []
        for item_id, item in self.items.items():
            if query.lower() in item["text"].lower():
                hits.append({"item_id": item_id, "text": item["text"], "score": 1.0,
                             "category": item["category"],
                             "confidence": item["confidence"]})
        return hits[:top_k]


# ------------------------------------------------------------------ registry


def test_scheme_dispatch_and_sqlite_default():
    register_backend("fake", FakeBackend)
    external = get_backend("fake://store-a")
    assert isinstance(external, FakeBackend)
    assert isinstance(get_backend(":memory:"), SqliteMemoryBackend)
    # Unregistered schemes fall through to SQLite (it treats the URL as a
    # path) rather than erroring — matches pre-registry behavior.
    assert isinstance(get_backend("plain-file.db"), SqliteMemoryBackend)
    get_backend("plain-file.db").close()
    import os
    os.remove("plain-file.db")


def test_sqlite_file_uri_honors_shared_memory_mode(tmp_path, monkeypatch):
    """A SQLite shared-memory URI must never become a worktree file."""
    monkeypatch.chdir(tmp_path)
    db_url = f"file:mem_{uuid.uuid4().hex}?mode=memory&cache=shared"

    backend = SqliteMemoryBackend(db_url)
    try:
        database_path = backend._conn.execute("PRAGMA database_list").fetchone()[2]
        assert database_path == ""
        assert not (tmp_path / db_url).exists()
    finally:
        backend.close()


def test_backend_instances_are_singletons_per_url():
    register_backend("fake", FakeBackend)
    assert get_backend("fake://a") is get_backend("fake://a")
    assert get_backend("fake://a") is not get_backend("fake://b")


def test_external_backend_defaults_are_safe():
    """The base class no-ops everything except the two semantic methods."""
    backend = FakeBackend("fake://x")
    assert backend.find_by_text("anything") is None
    assert backend.get_subject("id") is None
    backend.set_subject("id", "user:a")     # no-op, no error
    backend.enforce_limit(10)               # no-op, no error
    backend.update_retrieval("id")          # no-op, no error
    backend.close()                         # no-op, no error
    assert backend.count() == 0
    assert isinstance(backend, MemoryBackend)  # satisfies the protocol


def test_tools_path_works_against_external_backend():
    """retrieve_memories_fn — the path chat recall uses — needs only the URL."""
    from packs.memory_gateway.tools import retrieve_memories_fn

    register_backend("fake", FakeBackend)
    get_backend("fake://tools").store_item("m1", "favorite color is teal")
    results = retrieve_memories_fn("teal", backend_url="fake://tools")
    assert [r["item_id"] for r in results] == ["m1"]


# ------------------------------------------------------------------ mem0


class FakeMem0Client:
    """Deterministic stand-in with the OSS mem0 Memory surface:
    add(text, user_id=..., metadata=...) / search(query, user_id=..., limit=...)
    returning {"results": [{"id", "memory", "score", "metadata"}]}."""

    def __init__(self):
        self.records: list[dict] = []

    def add(self, text, user_id=None, metadata=None):
        self.records.append({"id": f"mem0-{len(self.records)}", "memory": text,
                             "user_id": user_id, "metadata": metadata or {}})

    def search(self, query, user_id=None, limit=10):
        results = []
        for record in self.records:
            if record["user_id"] != user_id:
                continue
            if query.lower() in record["memory"].lower():
                score = 0.9
            elif set(query.lower().split()) & set(record["memory"].lower().split()):
                score = 0.5
            else:
                score = 0.05
            results.append({**record, "score": score})
        results.sort(key=lambda r: r["score"], reverse=True)
        return {"results": results[:limit]}


def _mem0_backend() -> tuple[Mem0Backend, FakeMem0Client]:
    client = FakeMem0Client()
    return Mem0Backend("mem0://default", client=client), client


def test_mem0_store_maps_subject_to_user_id():
    backend, client = _mem0_backend()
    backend.store_item("item-1", "prefers dark mode", category="preference",
                       confidence=0.8, subject_ref="user:alice",
                       metadata={"frame_id": "f1"})
    backend.store_item("item-2", "office wifi password rotates monthly")

    assert client.records[0]["user_id"] == "user:alice"
    assert client.records[0]["metadata"]["item_id"] == "item-1"
    assert client.records[0]["metadata"]["category"] == "preference"
    assert client.records[0]["metadata"]["frame_id"] == "f1"
    # Subject-less memory lands under the global user id.
    assert client.records[1]["user_id"] == "global"


def test_mem0_retrieval_roundtrip_and_result_shape():
    backend, _ = _mem0_backend()
    backend.store_item("item-1", "prefers dark mode", category="preference",
                       confidence=0.8, subject_ref="user:alice")
    results = backend.retrieve_by_query("dark mode", subject_ref="user:alice",
                                        subject_scoped=True)
    assert len(results) == 1
    hit = results[0]
    assert hit["item_id"] == "item-1"
    assert hit["text"] == "prefers dark mode"
    assert hit["category"] == "preference"
    assert hit["confidence"] == 0.8
    assert 0.0 <= hit["score"] <= 1.0


def test_mem0_subject_scoping_and_global_merge():
    backend, _ = _mem0_backend()
    backend.store_item("a1", "alice likes tea", subject_ref="user:alice")
    backend.store_item("b1", "bob likes tea", subject_ref="user:bob")
    backend.store_item("g1", "the team drinks tea on fridays")  # global

    scoped = backend.retrieve_by_query("tea", subject_ref="user:alice",
                                       subject_scoped=True, include_global=True,
                                       min_score=0.1)
    ids = {r["item_id"] for r in scoped}
    assert "a1" in ids and "g1" in ids and "b1" not in ids

    strict = backend.retrieve_by_query("tea", subject_ref="user:alice",
                                       subject_scoped=True, include_global=False,
                                       min_score=0.1)
    assert {r["item_id"] for r in strict} == {"a1"}


def test_mem0_min_score_category_and_frame_filters():
    backend, _ = _mem0_backend()
    backend.store_item("i1", "favorite color teal", category="preference",
                       subject_ref="u", metadata={"frame_id": "f9"})
    backend.store_item("i2", "sky color notes", category="fact", subject_ref="u")

    # min_score: the weak-overlap item (0.5 fake score) is excluded at 0.6.
    high_bar = backend.retrieve_by_query("favorite color teal", subject_ref="u",
                                         subject_scoped=True, min_score=0.6)
    assert {r["item_id"] for r in high_bar} == {"i1"}

    only_pref = backend.retrieve_by_query("color", subject_ref="u",
                                          subject_scoped=True, min_score=0.1,
                                          category="preference")
    assert {r["item_id"] for r in only_pref} == {"i1"}

    # Same-frame exclusion: a memory born in the asking frame never answers it.
    excluded = backend.retrieve_by_query("favorite color teal", subject_ref="u",
                                         subject_scoped=True, min_score=0.1,
                                         exclude_frame_id="f9")
    assert all(r["item_id"] != "i1" for r in excluded)


def test_mem0_degrades_gracefully():
    """A failing client or odd result shapes yield empty results, not errors."""

    class ExplodingClient:
        def search(self, *a, **k):
            raise RuntimeError("service down")

        def add(self, *a, **k):
            raise RuntimeError("service down")

    backend = Mem0Backend("mem0://default", client=ExplodingClient())
    assert backend.retrieve_by_query("anything") == []

    class WeirdShapeClient:
        def search(self, *a, **k):
            return {"results": [{"no_memory_key": True}, "not-a-dict", None]}

    backend = Mem0Backend("mem0://default", client=WeirdShapeClient())
    assert backend.retrieve_by_query("anything") == []


def test_mem0_requires_package_or_client():
    """Without an injected client, the constructor needs the mem0 package and
    says so actionably (this environment doesn't have it installed)."""
    try:
        import mem0  # noqa: F401
        pytest.skip("mem0 installed here; lazy-import error path not reachable")
    except ImportError:
        pass
    with pytest.raises(ImportError, match="mem0"):
        Mem0Backend("mem0://default")


def test_register_mem0_backend_wires_scheme():
    client = FakeMem0Client()
    register_mem0_backend(client=client)
    backend = get_backend("mem0://default")
    assert isinstance(backend, Mem0Backend)
    backend.store_item("x", "hello world", subject_ref="u")
    assert client.records, "registered backend must reach the injected client"
