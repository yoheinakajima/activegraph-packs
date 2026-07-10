"""Tree semantics for the official ChatGPT export importer.

The export's ``mapping`` is not a transcript list: edits and regenerations make
it a tree.  The current-node ancestry is canonical, while every message outside
that path remains correction evidence instead of being silently discarded.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from activegraph import Graph, Runtime

sys.path.insert(0, str(Path(__file__).parents[1]))

from packs.activity_normalizer import (
    ActivityNormalizerSettings,
    pack as activity_normalizer_pack,
)
from packs.core import CoreSettings, pack as core_pack
from packs.importers.chatgpt_export import (
    ChatGPTExportSettings,
    pack as chatgpt_export_pack,
)
from packs.importers.chatgpt_export.tools import import_chatgpt_export_fn


def _message(node_id: str, role: str, text: str, created_at: int) -> dict:
    return {
        "id": node_id,
        "author": {"role": role, "name": None, "metadata": {}},
        "create_time": created_at,
        "update_time": None,
        "content": {"content_type": "text", "parts": [text]},
        "status": "finished_successfully",
        "end_turn": role == "assistant",
        "weight": 1.0,
        "metadata": {},
        "recipient": "all",
    }


def _write_branching_export(path: Path) -> None:
    conversation = {
        "id": "conversation-branching",
        "conversation_id": "conversation-branching",
        "title": "A regenerated response",
        "create_time": 1_704_067_200,
        "update_time": 1_704_067_320,
        "current_node": "assistant-new",
        "mapping": {
            "root": {
                "id": "root",
                "message": None,
                "parent": None,
                "children": ["user-question"],
            },
            "user-question": {
                "id": "user-question",
                "message": _message(
                    "user-question",
                    "user",
                    "Which deployment day should we use?",
                    1_704_067_200,
                ),
                "parent": "root",
                "children": ["assistant-old", "assistant-new"],
            },
            "assistant-old": {
                "id": "assistant-old",
                "message": _message(
                    "assistant-old",
                    "assistant",
                    "Use Tuesday.",
                    1_704_067_260,
                ),
                "parent": "user-question",
                "children": [],
            },
            "assistant-new": {
                "id": "assistant-new",
                "message": _message(
                    "assistant-new",
                    "assistant",
                    "Use Wednesday after the correction.",
                    1_704_067_320,
                ),
                "parent": "user-question",
                "children": [],
            },
        },
    }
    payload = json.dumps(
        [conversation],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    member = ZipInfo("conversations.json", date_time=(2024, 1, 1, 0, 0, 0))
    member.compress_type = ZIP_DEFLATED
    member.external_attr = 0o600 << 16
    with ZipFile(path, "w") as archive:
        archive.writestr(member, payload)


def test_chatgpt_tree_marks_current_leaf_path_and_preserves_abandoned_branch(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    export_path = tmp_path / "chatgpt-export.zip"
    _write_branching_export(export_path)

    normalizer_settings = ActivityNormalizerSettings(
        artifact_store_dir=str(artifact_dir)
    )
    runtime = Runtime(Graph())
    runtime.load_pack(core_pack, settings=CoreSettings())
    runtime.load_pack(activity_normalizer_pack, settings=normalizer_settings)
    runtime.load_pack(
        chatgpt_export_pack,
        settings=ChatGPTExportSettings(artifact_store_dir=str(artifact_dir)),
    )

    result = import_chatgpt_export_fn(
        runtime.graph,
        str(export_path),
        "surface_chatgpt_acceptance",
        artifact_store_dir=str(artifact_dir),
        replay_mode="artifact",
        is_fixture=False,
    )
    runtime.run_until_idle()

    assert result["ok"] is True
    assert result["imported"] == 3
    assert result["failed"] == 0

    acquired_items = {
        item.data["provider_item_id"]: item
        for item in runtime.graph.objects(type="acquired_item")
    }
    assert set(acquired_items) == {
        "conversation-branching:user-question",
        "conversation-branching:assistant-old",
        "conversation-branching:assistant-new",
    }

    contents = {
        content.data["acquired_item_id"]: content
        for content in runtime.graph.objects(type="acquired_content")
    }

    def metadata(node_id: str) -> dict:
        item = acquired_items[f"conversation-branching:{node_id}"]
        return contents[item.id].data["normalized_metadata"]

    user_meta = metadata("user-question")
    old_meta = metadata("assistant-old")
    new_meta = metadata("assistant-new")
    assert user_meta["branch_status"] == "canonical"
    assert new_meta["branch_status"] == "canonical"
    assert old_meta["branch_status"] == "abandoned"
    assert user_meta["canonical_path_index"] < new_meta["canonical_path_index"]
    assert old_meta["canonical_path_index"] is None
    assert old_meta["correction_signal"] is True

    evidence = list(runtime.graph.objects(type="activity_evidence"))
    assert len(evidence) == 3
    evidence_by_node = {
        item.data["normalized_metadata"]["node_id"]: item for item in evidence
    }
    assert evidence_by_node["assistant-new"].data["normalized_metadata"][
        "branch_status"
    ] == "canonical"
    assert evidence_by_node["assistant-old"].data["normalized_metadata"][
        "branch_status"
    ] == "abandoned"
    assert evidence_by_node["assistant-old"].data["normalized_content"] == (
        "Use Tuesday."
    )
