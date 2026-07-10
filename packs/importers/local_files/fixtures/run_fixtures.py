"""Deterministic, offline fixtures for the Local Files importer."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parents[3]))

from activegraph import Graph, Runtime

from packs.activity_normalizer import (
    ActivityNormalizerSettings,
    pack as activity_normalizer_pack,
)
from packs.core import pack as core_pack
from packs.importers.local_files import pack as local_files_pack
from packs.importers.local_files.tools import import_local_files_fn


def _runtime(artifact_store: Path) -> tuple[Graph, Runtime]:
    graph = Graph()
    runtime = Runtime(graph)
    runtime.load_pack(core_pack)
    runtime.load_pack(
        activity_normalizer_pack,
        settings=ActivityNormalizerSettings(artifact_store_dir=str(artifact_store)),
    )
    runtime.load_pack(local_files_pack)
    return graph, runtime


def run_sorted_artifact_fixture() -> dict:
    """Sorted snapshot emits paired records, artifacts, and stable progress."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "knowledge"
        store = base / "replay"
        (root / "nested").mkdir(parents=True)
        (root / "z.md").write_text("# Last\n\nA deterministic note.\n", encoding="utf-8")
        (root / "a.txt").write_text("The user prefers concise reports.\n", encoding="utf-8")
        (root / "nested" / "b.json").write_text(
            '{"task": "prepare the weekly brief", "done": false}\n',
            encoding="utf-8",
        )
        # Symlinked files are never acquisition inputs.
        try:
            (root / "linked.txt").symlink_to(root / "a.txt")
        except (OSError, NotImplementedError):
            pass

        graph, runtime = _runtime(store)
        result = import_local_files_fn(
            graph,
            str(root),
            "surface_local_fixture",
            artifact_store_dir=str(store),
            is_fixture=True,
        )
        runtime.run_until_idle()

        assert result["ok"] is True
        assert result["imported"] == 3
        items = list(graph.objects(type="acquired_item"))
        assert [o.data["provider_item_id"] for o in items] == [
            "a.txt",
            "nested/b.json",
            "z.md",
        ]
        contents = list(graph.objects(type="acquired_content"))
        assert len(contents) == 3
        assert all(o.data["source_category"] == "local_knowledge" for o in contents)
        assert all(o.data["connection_path"] == "local" for o in contents)
        assert all(o.data["is_fixture"] is True for o in contents)

        for item in items:
            digest = item.data["replay_payload_hash"]
            assert item.data["replay_payload_ref"] == (
                f"artifact://sha256/{digest[:2]}/{digest}"
            )
            assert (store / "sha256" / digest[:2] / digest).is_file()

        cursors = list(graph.objects(type="backfill_cursor"))
        assert len(cursors) == 1
        assert cursors[0].data["oldest_ingested_ref"] == "a.txt"
        assert cursors[0].data["newest_ingested_ref"] == "z.md"
        return {"imported": 3, "artifacts": 3, "cursor": result["cursor_id"]}


def run_malformed_json_fixture() -> dict:
    """A malformed JSON file records failure and no acquired pair for it."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "knowledge"
        root.mkdir()
        (root / "good.txt").write_text("A valid sibling remains committed.\n", encoding="utf-8")
        (root / "bad.json").write_text('{"unterminated": ', encoding="utf-8")

        graph, runtime = _runtime(base / "replay")
        result = import_local_files_fn(
            graph,
            str(root),
            "surface_malformed_fixture",
            artifact_store_dir=str(base / "replay"),
            is_fixture=True,
        )
        runtime.run_until_idle()

        assert result["ok"] is False
        assert result["imported"] == 1
        assert result["failed"] == 1
        items = list(graph.objects(type="acquired_item"))
        assert [o.data["provider_item_id"] for o in items] == ["good.txt"]
        failures = list(graph.objects(type="ingestion_failure"))
        assert len(failures) == 1
        assert failures[0].data["error_code"] == "invalid_json"
        assert failures[0].data["metadata"]["provider_item_id"] == "bad.json"
        return {"imported": 1, "failures": 1, "partial_bad_item": False}


def run_reference_only_fixture() -> dict:
    """Reference-only keeps derived content but never retains replay bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "knowledge"
        root.mkdir()
        (root / "private.md").write_text(
            "# Licensed note\n\nThe user prefers the morning review.\n",
            encoding="utf-8",
        )

        graph, runtime = _runtime(base / "replay")
        result = import_local_files_fn(
            graph,
            str(root),
            "surface_reference_fixture",
            artifact_store_dir=str(base / "replay"),
            replay_mode="reference_only",
            is_fixture=True,
        )
        runtime.run_until_idle()

        assert result["imported"] == 1
        item = list(graph.objects(type="acquired_item"))[0]
        assert item.data["replay_payload_ref"] == "reference_only:no-payload"
        assert not (base / "replay").exists(), "reference-only must write no replay artifact"
        content = list(graph.objects(type="acquired_content"))[0]
        assert "Licensed note" in content.data["normalized_content"]
        evidence = list(graph.objects(type="activity_evidence"))
        assert len(evidence) == 1 and evidence[0].data["replay_complete"] is False
        return {"imported": 1, "replay_complete": False}


def run_all() -> bool:
    print("=" * 60)
    print("Local Files Importer Fixtures")
    print("=" * 60)

    scenarios = [
        ("sorted artifact snapshot", run_sorted_artifact_fixture),
        ("malformed JSON isolation", run_malformed_json_fixture),
        ("reference-only policy", run_reference_only_fixture),
    ]
    for label, scenario in scenarios:
        print(f"\n- {label}")
        print(f"  PASS: {scenario()}")

    print("\nALL PASS")
    return True


if __name__ == "__main__":
    try:
        ok = run_all()
    except AssertionError as exc:
        print(f"\nFAIL: {exc}")
        sys.exit(1)
    sys.exit(0 if ok else 1)
