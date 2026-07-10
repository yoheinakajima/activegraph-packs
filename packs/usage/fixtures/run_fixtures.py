"""Offline P1→P2 source-zero dogfood with two settled source categories."""

from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parents[2]))

from activegraph import Graph, Runtime, TickingClock

from packs.activity_normalizer import ActivityNormalizerSettings
from packs.activity_normalizer import pack as activity_normalizer_pack
from packs.core import CoreSettings
from packs.core import pack as core_pack
from packs.importers.chatgpt_export import ChatGPTExportSettings
from packs.importers.chatgpt_export import pack as chatgpt_export_pack
from packs.importers.chatgpt_export.tools import import_chatgpt_export_fn
from packs.importers.local_files import LocalFilesSettings
from packs.importers.local_files import pack as local_files_pack
from packs.importers.local_files.tools import import_local_files_fn
from packs.usage import UsageSettings
from packs.usage import pack as usage_pack
from packs.usage.tools import connect_surface_fn, list_surfaces_fn, project_usage_fn


def _vision_source(tmp: Path, *, use_live_vision: bool = True) -> tuple[Path, str]:
    live = Path("/tmp/vision")
    if use_live_vision and live.is_dir():
        supported = [
            path
            for path in live.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() in {".txt", ".md", ".markdown", ".json"}
            and ".git" not in path.parts
        ]
        if len(supported) >= 25:
            return live, "live-/tmp/vision"

    snapshot = json.loads((_HERE / "vision_snapshot.json").read_text(encoding="utf-8"))
    root = tmp / "activegraph-vision"
    root.mkdir(parents=True)
    for item in snapshot["files"]:
        target = root / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item["content"], encoding="utf-8")
    return root, snapshot["snapshot_id"]


def _chat_message(node_id: str, role: str, text: str, provider_time: int) -> dict:
    return {
        "id": node_id,
        "author": {"role": role, "name": None, "metadata": {}},
        "create_time": provider_time,
        "update_time": None,
        "content": {"content_type": "text", "parts": [text]},
        "status": "finished_successfully",
        "end_turn": role == "assistant",
        "weight": 1.0,
        "metadata": {},
        "recipient": "all",
    }


def _write_chatgpt_export(path: Path) -> None:
    conversation = {
        "id": "dogfood-chatgpt",
        "conversation_id": "dogfood-chatgpt",
        "title": "Provider-time coverage",
        "create_time": 1_704_067_200,
        "update_time": 1_704_326_400,
        "current_node": "assistant-day-four",
        "mapping": {
            "root": {
                "id": "root",
                "message": None,
                "parent": None,
                "children": ["user-day-one"],
            },
            "user-day-one": {
                "id": "user-day-one",
                "message": _chat_message(
                    "user-day-one", "user", "Remember this preference.", 1_704_067_200
                ),
                "parent": "root",
                "children": ["assistant-day-four"],
            },
            "assistant-day-four": {
                "id": "assistant-day-four",
                "message": _chat_message(
                    "assistant-day-four",
                    "assistant",
                    "The preference is retained as evidence.",
                    1_704_326_400,
                ),
                "parent": "user-day-one",
                "children": [],
            },
        },
    }
    payload = json.dumps(
        [conversation], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    member = zipfile.ZipInfo("conversations.json", date_time=(2024, 1, 1, 0, 0, 0))
    member.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member, payload)


def run_dogfood_fixture(*, use_live_vision: bool = True) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        artifacts = tmp / "replay-artifacts"
        vision_root, snapshot_kind = _vision_source(
            tmp, use_live_vision=use_live_vision
        )
        chatgpt_zip = tmp / "chatgpt-export.zip"
        _write_chatgpt_export(chatgpt_zip)

        runtime = Runtime(
            Graph(clock=TickingClock("2024-02-01T00:00:00Z", step_seconds=1))
        )
        runtime.load_pack(core_pack, settings=CoreSettings())
        runtime.load_pack(
            activity_normalizer_pack,
            settings=ActivityNormalizerSettings(artifact_store_dir=str(artifacts)),
        )
        runtime.load_pack(usage_pack, settings=UsageSettings())
        runtime.load_pack(
            local_files_pack,
            settings=LocalFilesSettings(artifact_store_dir=str(artifacts)),
        )
        runtime.load_pack(
            chatgpt_export_pack,
            settings=ChatGPTExportSettings(artifact_store_dir=str(artifacts)),
        )

        connect_surface_fn(
            runtime.graph,
            "surface_vision_core",
            "local_knowledge",
            provider={"name": "ActiveGraph Vision", "coverage_role": "core"},
            path="local",
            adapter="local_files",
            acquisition_mode="snapshot",
            privacy_scope="workspace",
        )
        connect_surface_fn(
            runtime.graph,
            "surface_chatgpt_personal",
            "ai_activity",
            provider={"name": "ChatGPT", "coverage_role": "personal"},
            path="export",
            adapter="chatgpt_export",
            acquisition_mode="snapshot",
            privacy_scope="account",
        )

        local_result = import_local_files_fn(
            runtime.graph,
            str(vision_root),
            "surface_vision_core",
            artifact_store_dir=str(artifacts),
            replay_mode="artifact",
            is_fixture=False,
            max_files=1000,
        )
        chat_result = import_chatgpt_export_fn(
            runtime.graph,
            str(chatgpt_zip),
            "surface_chatgpt_personal",
            artifact_store_dir=str(artifacts),
            replay_mode="artifact",
            is_fixture=False,
        )
        assert local_result["imported"] >= 25, local_result
        assert chat_result["imported"] == 2, chat_result

        runtime.run_until_idle()
        horizon = runtime.graph.events[-1].id
        projection = project_usage_fn(runtime.graph, horizon)
        surfaces = list_surfaces_fn(runtime.graph, horizon)
        by_id = {surface["id"]: surface for surface in surfaces}

        local = by_id["surface_vision_core"]
        chatgpt = by_id["surface_chatgpt_personal"]
        assert local["status"] == "settled", local
        assert local["settlement"]["passed_by"] in {"volume", "both"}
        assert local["coverage"]["unique_evidence"] >= 25
        assert chatgpt["status"] == "settled", chatgpt
        assert chatgpt["settlement"]["passed_by"] in {"coverage", "both"}
        assert chatgpt["coverage"]["coverage_days"] >= 3

        settled_categories = {
            surface["category"] for surface in surfaces if surface["status"] == "settled"
        }
        assert {"local_knowledge", "ai_activity"} <= settled_categories
        assert local["provider"]["coverage_role"] == "core"
        assert chatgpt["provider"]["coverage_role"] == "personal"
        assert projection["event_horizon_event_id"] == horizon
        return {
            "ok": True,
            "snapshot": snapshot_kind,
            "horizon": horizon,
            "settled_categories": sorted(settled_categories),
        }


def main() -> None:
    result = run_dogfood_fixture()
    print(f"Usage dogfood fixture: PASS {result}")


if __name__ == "__main__":
    main()
