"""Deterministic, offline fixtures for the ChatGPT export tree parser."""

from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parents[3]))

from activegraph import Graph
from packs.importers.chatgpt_export.tools import import_chatgpt_export_fn


def _write_export(path: Path, payload) -> None:
    info = zipfile.ZipInfo("conversations.json", date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(info, json.dumps(payload, sort_keys=True).encode("utf-8"))


def _branched_conversation() -> list[dict]:
    return [
        {
            "id": "conversation-1",
            "title": "A corrected answer",
            "current_node": "assistant-current",
            "mapping": {
                "root": {
                    "id": "root",
                    "parent": None,
                    "children": ["user-1"],
                    "message": None,
                },
                "user-1": {
                    "id": "user-1",
                    "parent": "root",
                    "children": ["assistant-old", "assistant-current"],
                    "message": {
                        "id": "user-1",
                        "author": {"role": "user"},
                        "create_time": 1_700_000_000,
                        "status": "finished_successfully",
                        "content": {"content_type": "text", "parts": ["Please revise this."]},
                    },
                },
                "assistant-old": {
                    "id": "assistant-old",
                    "parent": "user-1",
                    "children": [],
                    "message": {
                        "id": "assistant-old",
                        "author": {"role": "assistant"},
                        "create_time": 1_700_000_001,
                        "status": "finished_successfully",
                        "content": {"content_type": "text", "parts": ["Abandoned answer"]},
                    },
                },
                "assistant-current": {
                    "id": "assistant-current",
                    "parent": "user-1",
                    "children": [],
                    "message": {
                        "id": "assistant-current",
                        "author": {"role": "assistant"},
                        "create_time": 1_700_000_002,
                        "status": "finished_successfully",
                        "content": {"content_type": "text", "parts": ["Current answer"]},
                    },
                },
            },
        }
    ]


def run_tree_fixture(tmp: Path) -> None:
    archive = tmp / "export.zip"
    _write_export(archive, _branched_conversation())
    graph = Graph()
    result = import_chatgpt_export_fn(
        graph,
        str(archive),
        "surface_chatgpt",
        artifact_store_dir=str(tmp / "artifacts"),
    )
    assert result["ok"] is True
    assert result["imported"] == 3
    assert result["canonical_messages"] == 2
    assert result["abandoned_messages"] == 1
    content = list(graph.objects(type="acquired_content"))
    abandoned = [o for o in content if o.data["normalized_metadata"]["correction_signal"]]
    assert len(abandoned) == 1
    assert abandoned[0].data["normalized_content"] == "Abandoned answer"
    assert abandoned[0].data["normalized_metadata"]["branch_status"] == "abandoned"
    items = list(graph.objects(type="acquired_item"))
    assert all(item.data["replay_payload_ref"].startswith("artifact://sha256/") for item in items)


def run_malformed_fixture(tmp: Path) -> None:
    archive = tmp / "malformed.zip"
    _write_export(archive, {"not": "a list"})
    graph = Graph()
    result = import_chatgpt_export_fn(graph, str(archive), "surface_bad")
    assert result["ok"] is False
    assert result["imported"] == 0
    assert len(list(graph.objects(type="ingestion_failure"))) == 1
    assert not list(graph.objects(type="acquired_item"))


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        run_tree_fixture(tmp)
        run_malformed_fixture(tmp)
    print("ChatGPT Export fixtures: ALL PASS")


if __name__ == "__main__":
    main()
