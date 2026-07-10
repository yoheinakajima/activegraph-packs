"""Deterministic, offline fixtures for the Claude export flat-list parser."""

from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parents[3]))

from activegraph import Graph, Runtime

from packs.activity_normalizer import (
    ActivityNormalizerSettings,
    pack as activity_normalizer_pack,
)
from packs.core import pack as core_pack
from packs.importers.claude_export import pack as claude_export_pack
from packs.importers.claude_export.tools import import_claude_export_fn


def _runtime(artifact_store: Path) -> tuple[Graph, Runtime]:
    graph = Graph()
    runtime = Runtime(graph)
    runtime.load_pack(core_pack)
    runtime.load_pack(
        activity_normalizer_pack,
        settings=ActivityNormalizerSettings(artifact_store_dir=str(artifact_store)),
    )
    runtime.load_pack(claude_export_pack)
    return graph, runtime


def _write_zip_export(path: Path, payload) -> None:
    info = zipfile.ZipInfo("conversations.json", date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(info, json.dumps(payload, sort_keys=True).encode("utf-8"))


def _message(uuid: str, sender: str, text: str, created_at: str, **extra) -> dict:
    return {
        "uuid": uuid,
        "text": text,
        "content": [{"type": "text", "text": text}],
        "sender": sender,
        "created_at": created_at,
        "updated_at": created_at,
        **extra,
    }


def _flat_conversations() -> list[dict]:
    return [
        {
            "uuid": "conversation-1",
            "name": "Weekly planning",
            "created_at": "2026-01-05T09:00:00.000000+00:00",
            "updated_at": "2026-01-05T09:02:00.000000+00:00",
            "chat_messages": [
                _message(
                    "msg-1",
                    "human",
                    "Please draft the weekly plan.",
                    "2026-01-05T09:00:00.000000+00:00",
                ),
                _message(
                    "msg-2",
                    "assistant",
                    "Here is the weekly plan draft.",
                    "2026-01-05T09:01:00.000000+00:00",
                ),
            ],
        },
        {
            "uuid": "conversation-2",
            "name": "Tool use",
            "created_at": "2026-01-06T10:00:00.000000+00:00",
            "updated_at": "2026-01-06T10:01:00.000000+00:00",
            "chat_messages": [
                _message(
                    "msg-3",
                    "human",
                    "Run the report.",
                    "2026-01-06T10:00:00.000000+00:00",
                    content=[
                        {"type": "text", "text": "Run the report."},
                        {"type": "tool_use", "name": "report_runner"},
                    ],
                ),
            ],
        },
    ]


def run_zip_fixture() -> dict:
    """The official ZIP emits paired records, evidence, and stable progress."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        archive = base / "claude-export.zip"
        store = base / "replay"
        _write_zip_export(archive, _flat_conversations())

        graph, runtime = _runtime(store)
        result = import_claude_export_fn(
            graph,
            str(archive),
            "surface_claude_fixture",
            artifact_store_dir=str(store),
            is_fixture=True,
        )
        runtime.run_until_idle()

        assert result["ok"] is True
        assert result["imported"] == 3
        assert result["conversations"] == 2
        assert result["human_messages"] == 2
        assert result["assistant_messages"] == 1

        items = list(graph.objects(type="acquired_item"))
        assert sorted(o.data["provider_item_id"] for o in items) == [
            "conversation-1:msg-1",
            "conversation-1:msg-2",
            "conversation-2:msg-3",
        ]
        contents = list(graph.objects(type="acquired_content"))
        assert len(contents) == 3
        assert all(o.data["source_category"] == "ai_activity" for o in contents)
        assert all(o.data["connection_path"] == "export" for o in contents)
        assert all(o.data["is_fixture"] is True for o in contents)
        omitted = [
            o
            for o in contents
            if "[tool_use content omitted]" in o.data["normalized_content"]
        ]
        assert len(omitted) == 1

        for item in items:
            digest = item.data["replay_payload_hash"]
            assert item.data["replay_payload_ref"] == (
                f"artifact://sha256/{digest[:2]}/{digest}"
            )
            assert (store / "sha256" / digest[:2] / digest).is_file()

        evidence = list(graph.objects(type="activity_evidence"))
        assert len(evidence) == 3

        cursors = list(graph.objects(type="backfill_cursor"))
        assert len(cursors) == 1
        assert cursors[0].data["oldest_ingested_ref"] == "conversation-1:msg-1"
        assert cursors[0].data["newest_ingested_ref"] == "conversation-2:msg-3"
        return {"imported": 3, "evidence": 3, "cursor": result["cursor_id"]}


def run_bare_json_fixture() -> dict:
    """A bare, already-unzipped conversations.json is accepted directly."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        bare = base / "conversations.json"
        bare.write_text(
            json.dumps(_flat_conversations(), sort_keys=True), encoding="utf-8"
        )

        graph, runtime = _runtime(base / "replay")
        result = import_claude_export_fn(
            graph,
            str(bare),
            "surface_claude_bare_fixture",
            artifact_store_dir=str(base / "replay"),
            is_fixture=True,
        )
        runtime.run_until_idle()

        assert result["ok"] is True
        assert result["imported"] == 3
        items = list(graph.objects(type="acquired_item"))
        assert len(items) == 3
        assert all("!/" not in o.data["source_ref"] for o in items)
        evidence = list(graph.objects(type="activity_evidence"))
        assert len(evidence) == 3
        return {"imported": 3, "evidence": 3}


def run_malformed_conversation_fixture() -> dict:
    """A malformed conversation records failure; valid siblings survive."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        archive = base / "claude-export.zip"
        malformed = {
            "uuid": "conversation-bad",
            "name": "Broken",
            "chat_messages": [{"text": "message with no uuid", "sender": "human"}],
        }
        _write_zip_export(archive, [malformed, _flat_conversations()[0]])

        graph, runtime = _runtime(base / "replay")
        result = import_claude_export_fn(
            graph,
            str(archive),
            "surface_claude_malformed_fixture",
            artifact_store_dir=str(base / "replay"),
            is_fixture=True,
        )
        runtime.run_until_idle()

        assert result["ok"] is False
        assert result["imported"] == 2
        assert result["failed"] == 1
        failures = list(graph.objects(type="ingestion_failure"))
        assert len(failures) == 1
        assert failures[0].data["stage"] == "acquisition"
        assert failures[0].data["error_code"] == "invalid_conversation"
        assert failures[0].data["metadata"]["conversation_uuid"] == "conversation-bad"
        items = list(graph.objects(type="acquired_item"))
        assert sorted(o.data["provider_item_id"] for o in items) == [
            "conversation-1:msg-1",
            "conversation-1:msg-2",
        ]
        evidence = list(graph.objects(type="activity_evidence"))
        assert len(evidence) == 2
        return {"imported": 2, "failures": 1, "sibling_evidence": 2}


def run_all() -> bool:
    print("=" * 60)
    print("Claude Export Importer Fixtures")
    print("=" * 60)

    scenarios = [
        ("official ZIP snapshot", run_zip_fixture),
        ("bare conversations.json snapshot", run_bare_json_fixture),
        ("malformed conversation isolation", run_malformed_conversation_fixture),
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
