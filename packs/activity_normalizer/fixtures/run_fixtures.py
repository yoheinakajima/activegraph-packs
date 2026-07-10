"""Deterministic offline fixtures for Activity Normalizer."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parents[2]))

from activegraph import Graph, Runtime

from packs.activity_normalizer import pack
from packs.core import pack as core_pack


def run_inline_fixture() -> dict:
    graph = Graph()
    runtime = Runtime(graph)
    runtime.load_pack(core_pack)
    runtime.load_pack(pack)
    payload = "Remember: deterministic evidence survives retries."
    digest = hashlib.sha256(payload.encode()).hexdigest()

    def acquire() -> None:
        item = graph.add_object(
            "acquired_item",
            {
                "source_surface_id": "surface_inline_fixture",
                "provider_item_id": "message-1",
                "dedup_key": "message-1",
                "source_ref": "fixture:message-1",
                "source_hash": digest,
                "provider_time": "2026-01-01T00:00:00Z",
                "replay_mode": "inline",
                "replay_payload_ref": payload,
                "replay_payload_hash": digest,
                "media_type": "text/plain",
                "importer_id": "fixture",
                "importer_version": "0.1.0",
            },
        )
        graph.add_object(
            "acquired_content",
            {
                "acquired_item_id": item.id,
                "normalized_content": payload,
                "normalized_metadata": {},
                "source_category": "ai_activity",
                "connection_path": "pack",
                "is_fixture": True,
            },
        )

    acquire()
    runtime.run_until_idle()
    assert len(graph.objects(type="activity_evidence")) == 1
    assert len(graph.objects(type="extraction_record")) == 1
    assert graph.objects(type="memory_candidate")

    acquire()
    runtime.run_until_idle()
    assert len(graph.objects(type="activity_evidence")) == 1
    assert len(graph.objects(type="extraction_record")) == 1
    return {"evidence": 1, "retry": "no-op"}


def run_all() -> bool:
    print("Activity Normalizer Fixtures")
    print(f"  PASS: {run_inline_fixture()}")
    print("ALL PASS")
    return True


if __name__ == "__main__":
    try:
        ok = run_all()
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)
    sys.exit(0 if ok else 1)
