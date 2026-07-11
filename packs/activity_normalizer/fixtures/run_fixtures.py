"""Deterministic offline fixtures for Activity Normalizer.

Post-migration (ADR 0026 steps 2-3): the normalizer owns identity,
revisions, and replay; extraction runs on the shared annotation layer
and the compatibility projectors mint the legacy candidate types from
annotations. The direct evidence→candidate write path exists only as an
explicit legacy opt-in.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parents[2]))

from activegraph import Graph, Runtime

from packs.activity_normalizer import ActivityNormalizerSettings, pack
from packs.core import pack as core_pack

PAYLOAD = "Remember: deterministic evidence survives retries."
DIGEST = hashlib.sha256(PAYLOAD.encode()).hexdigest()


def _acquire(graph) -> None:
    item = graph.add_object(
        "acquired_item",
        {
            "source_surface_id": "surface_inline_fixture",
            "provider_item_id": "message-1",
            "dedup_key": "message-1",
            "source_ref": "fixture:message-1",
            "source_hash": DIGEST,
            "provider_time": "2026-01-01T00:00:00Z",
            "replay_mode": "inline",
            "replay_payload_ref": PAYLOAD,
            "replay_payload_hash": DIGEST,
            "media_type": "text/plain",
            "importer_id": "fixture",
            "importer_version": "0.1.0",
        },
    )
    graph.add_object(
        "acquired_content",
        {
            "acquired_item_id": item.id,
            "normalized_content": PAYLOAD,
            "normalized_metadata": {},
            "source_category": "ai_activity",
            "connection_path": "pack",
            "is_fixture": True,
        },
    )


def run_migrated_default_fixture() -> dict:
    """Default mode: evidence only — the direct candidate write path is
    disabled. With the shared layer loaded, the same candidates flow
    annotation-first (asserted by the semantic_extraction fixtures)."""
    graph = Graph()
    runtime = Runtime(graph)
    runtime.load_pack(core_pack)
    runtime.load_pack(pack)

    _acquire(graph)
    runtime.run_until_idle()
    assert len(graph.objects(type="activity_evidence")) == 1
    assert not graph.objects(type="extraction_record"), (
        "direct write path must be disabled by default"
    )
    assert not graph.objects(type="memory_candidate")

    _acquire(graph)
    runtime.run_until_idle()
    assert len(graph.objects(type="activity_evidence")) == 1
    return {"evidence": 1, "direct_write_path": "disabled", "retry": "no-op"}


def run_shared_path_fixture() -> dict:
    """The migrated flow end to end: shared-layer annotations, compat
    candidates with the legacy identity scheme, idempotent retry."""
    from packs.semantic_extraction import pack as semantic_pack

    graph = Graph()
    runtime = Runtime(graph)
    runtime.load_pack(core_pack)
    runtime.load_pack(pack)
    runtime.load_pack(semantic_pack)

    _acquire(graph)
    runtime.run_until_idle()
    assert len(graph.objects(type="activity_evidence")) == 1
    annotations = graph.objects(type="semantic_annotation")
    facets = {annotation.data["facet"] for annotation in annotations}
    assert "activity.memory" in facets, facets
    memory_candidates = graph.objects(type="memory_candidate")
    assert memory_candidates, "compat projector must mint the memory candidate"
    counts = (
        len(annotations),
        len(memory_candidates),
        len(graph.objects(type="extraction_run")),
    )

    _acquire(graph)
    runtime.run_until_idle()
    assert (
        len(graph.objects(type="semantic_annotation")),
        len(graph.objects(type="memory_candidate")),
        len(graph.objects(type="extraction_run")),
    ) == counts, "re-acquisition must add nothing"
    return {"annotations": counts[0], "memory_candidates": counts[1]}


def run_legacy_optin_fixture() -> dict:
    """The retained legacy path, selected explicitly (rollback lever)."""
    graph = Graph()
    runtime = Runtime(graph)
    runtime.load_pack(core_pack)
    runtime.load_pack(
        pack, settings=ActivityNormalizerSettings(legacy_extraction_enabled=True)
    )

    _acquire(graph)
    runtime.run_until_idle()
    assert len(graph.objects(type="activity_evidence")) == 1
    assert len(graph.objects(type="extraction_record")) == 1
    assert graph.objects(type="memory_candidate")

    _acquire(graph)
    runtime.run_until_idle()
    assert len(graph.objects(type="activity_evidence")) == 1
    assert len(graph.objects(type="extraction_record")) == 1
    return {"evidence": 1, "retry": "no-op"}


def run_all() -> bool:
    print("Activity Normalizer Fixtures")
    print(f"  [1] migrated default   PASS: {run_migrated_default_fixture()}")
    print(f"  [2] shared path        PASS: {run_shared_path_fixture()}")
    print(f"  [3] legacy opt-in      PASS: {run_legacy_optin_fixture()}")
    print("ALL PASS")
    return True


if __name__ == "__main__":
    try:
        ok = run_all()
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)
    sys.exit(0 if ok else 1)
