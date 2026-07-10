"""Flat-list semantics for the official Claude export importer.

Claude's ``conversations.json`` is a flat conversation list with ordered
``chat_messages`` transcripts — no branch tree.  Every message becomes one
strict acquired-item/content pair; the Activity Normalizer owns evidence
identity and deduplication, so identical re-imports must not grow evidence.
"""

from __future__ import annotations

import hashlib
import json
import shutil
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
from packs.importers.claude_export import (
    ClaudeExportSettings,
    pack as claude_export_pack,
)
from packs.importers.claude_export.tools import import_claude_export_fn


def _message(uuid: str, sender: str, text: str, created_at, **overrides) -> dict:
    message = {
        "uuid": uuid,
        "text": text,
        "content": [{"type": "text", "text": text}],
        "sender": sender,
        "created_at": created_at,
        "updated_at": created_at,
    }
    message.update(overrides)
    return message


def _conversation(uuid: str, name: str, messages: list[dict], **overrides) -> dict:
    conversation = {
        "uuid": uuid,
        "name": name,
        "created_at": "2026-01-05T09:00:00.000000+00:00",
        "updated_at": "2026-01-05T09:05:00.000000+00:00",
        "chat_messages": messages,
    }
    conversation.update(overrides)
    return conversation


def _flat_export() -> list[dict]:
    return [
        _conversation(
            "conversation-flat",
            "Deployment planning",
            [
                _message(
                    "msg-user",
                    "human",
                    "Which deployment day should we use?",
                    "2026-01-05T09:00:00.000000+00:00",
                ),
                _message(
                    "msg-assistant",
                    "assistant",
                    "Use Wednesday after the correction.",
                    "2026-01-05T09:01:00.000000+00:00",
                ),
            ],
        )
    ]


def _write_zip_export(path: Path, conversations: list[dict]) -> None:
    payload = json.dumps(
        conversations,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    member = ZipInfo("conversations.json", date_time=(2026, 1, 1, 0, 0, 0))
    member.compress_type = ZIP_DEFLATED
    member.external_attr = 0o600 << 16
    with ZipFile(path, "w") as archive:
        archive.writestr(member, payload)


def _write_bare_export(path: Path, conversations: list[dict]) -> None:
    path.write_text(
        json.dumps(conversations, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _runtime(artifact_dir: Path) -> Runtime:
    runtime = Runtime(Graph())
    runtime.load_pack(core_pack, settings=CoreSettings())
    runtime.load_pack(
        activity_normalizer_pack,
        settings=ActivityNormalizerSettings(artifact_store_dir=str(artifact_dir)),
    )
    runtime.load_pack(
        claude_export_pack,
        settings=ClaudeExportSettings(artifact_store_dir=str(artifact_dir)),
    )
    return runtime


def test_claude_zip_flat_transcript_emits_paired_records_and_evidence(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    export_path = tmp_path / "claude-export.zip"
    _write_zip_export(export_path, _flat_export())

    runtime = _runtime(artifact_dir)
    result = import_claude_export_fn(
        runtime.graph,
        str(export_path),
        "surface_claude_acceptance",
        artifact_store_dir=str(artifact_dir),
        replay_mode="artifact",
        is_fixture=False,
    )
    runtime.run_until_idle()

    assert result["ok"] is True
    assert result["imported"] == 2
    assert result["failed"] == 0
    assert result["conversations"] == 1
    assert result["human_messages"] == 1
    assert result["assistant_messages"] == 1

    acquired_items = {
        item.data["provider_item_id"]: item
        for item in runtime.graph.objects(type="acquired_item")
    }
    assert set(acquired_items) == {
        "conversation-flat:msg-user",
        "conversation-flat:msg-assistant",
    }
    for item in acquired_items.values():
        assert item.data["dedup_key"] == item.data["provider_item_id"]
        assert item.data["importer_id"] == "claude_export"
        assert item.data["importer_version"] == "0.1.0"
        assert item.data["media_type"] == "application/json"

    contents = {
        content.data["acquired_item_id"]: content
        for content in runtime.graph.objects(type="acquired_content")
    }
    assert len(contents) == 2
    user_item = acquired_items["conversation-flat:msg-user"]
    user_content = contents[user_item.id]
    assert user_content.data["source_category"] == "ai_activity"
    assert user_content.data["connection_path"] == "export"
    assert user_content.data["is_fixture"] is False
    assert user_content.data["normalized_content"] == (
        "Which deployment day should we use?"
    )
    metadata = user_content.data["normalized_metadata"]
    assert metadata["conversation_uuid"] == "conversation-flat"
    assert metadata["conversation_name"] == "Deployment planning"
    assert metadata["message_index"] == 0
    assert metadata["sender"] == "human"
    assert metadata["role"] == "user"
    assert metadata["content_source"] == "content_blocks"

    evidence = list(runtime.graph.objects(type="activity_evidence"))
    assert len(evidence) == 2
    by_uuid = {
        item.data["normalized_metadata"]["message_uuid"]: item for item in evidence
    }
    assert by_uuid["msg-assistant"].data["normalized_content"] == (
        "Use Wednesday after the correction."
    )
    assert by_uuid["msg-assistant"].data["normalized_metadata"]["sender"] == "assistant"


def test_bare_conversations_json_and_magic_detection(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    bare_path = tmp_path / "conversations.json"
    _write_bare_export(bare_path, _flat_export())

    runtime = _runtime(artifact_dir)
    result = import_claude_export_fn(
        runtime.graph,
        str(bare_path),
        "surface_claude_bare",
        artifact_store_dir=str(artifact_dir),
    )
    runtime.run_until_idle()

    assert result["ok"] is True
    assert result["imported"] == 2
    items = list(runtime.graph.objects(type="acquired_item"))
    assert all("!/" not in item.data["source_ref"] for item in items)
    assert len(list(runtime.graph.objects(type="activity_evidence"))) == 2

    # A ZIP payload without a .zip suffix is still detected by magic bytes.
    zip_path = tmp_path / "claude-export.zip"
    _write_zip_export(zip_path, _flat_export())
    disguised = tmp_path / "claude-export-download"
    shutil.copyfile(zip_path, disguised)
    other_runtime = _runtime(tmp_path / "artifacts2")
    disguised_result = import_claude_export_fn(
        other_runtime.graph,
        str(disguised),
        "surface_claude_magic",
        artifact_store_dir=str(tmp_path / "artifacts2"),
    )
    assert disguised_result["ok"] is True
    assert disguised_result["imported"] == 2
    magic_items = list(other_runtime.graph.objects(type="acquired_item"))
    assert all("!/conversations.json" in item.data["source_ref"] for item in magic_items)


def test_identical_reimport_reemits_items_but_evidence_identity_is_stable(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    export_path = tmp_path / "claude-export.zip"
    _write_zip_export(export_path, _flat_export())

    runtime = _runtime(artifact_dir)
    first = import_claude_export_fn(
        runtime.graph,
        str(export_path),
        "surface_claude_reimport",
        artifact_store_dir=str(artifact_dir),
    )
    runtime.run_until_idle()
    second = import_claude_export_fn(
        runtime.graph,
        str(export_path),
        "surface_claude_reimport",
        artifact_store_dir=str(artifact_dir),
    )
    runtime.run_until_idle()

    assert first["ok"] is True and second["ok"] is True
    # The importer intentionally re-emits acquisition records...
    assert len(list(runtime.graph.objects(type="acquired_item"))) == 4
    # ...while the normalizer keeps one current revision per evidence identity.
    evidence = list(runtime.graph.objects(type="activity_evidence"))
    assert len(evidence) == 2
    assert all(item.data["revision_number"] == 1 for item in evidence)
    assert all(item.data["status"] == "current" for item in evidence)
    cursors = list(runtime.graph.objects(type="backfill_cursor"))
    assert len(cursors) == 1


def test_unknown_blocks_are_omitted_and_plain_text_field_is_fallback(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    export_path = tmp_path / "claude-export.zip"
    conversations = [
        _conversation(
            "conversation-content",
            "Content shapes",
            [
                _message(
                    "msg-blocks",
                    "human",
                    "ignored plain text",
                    "2026-01-05T09:00:00.000000+00:00",
                    content=[
                        {"type": "text", "text": "Run the report."},
                        {"type": "tool_use", "name": "report_runner"},
                        {"type": "tool_result"},
                    ],
                ),
                {
                    "uuid": "msg-legacy",
                    "text": "Legacy message with only a text field.",
                    "sender": "assistant",
                    "created_at": "2026-01-05T09:01:00.000000+00:00",
                },
                {
                    "uuid": "msg-empty",
                    "sender": "assistant",
                    "content": None,
                    "text": None,
                },
            ],
        )
    ]
    _write_zip_export(export_path, conversations)

    runtime = _runtime(artifact_dir)
    result = import_claude_export_fn(
        runtime.graph,
        str(export_path),
        "surface_claude_content",
        artifact_store_dir=str(artifact_dir),
    )
    runtime.run_until_idle()

    assert result["ok"] is True
    assert result["imported"] == 3
    items = {
        item.data["provider_item_id"]: item
        for item in runtime.graph.objects(type="acquired_item")
    }
    contents = {
        content.data["acquired_item_id"]: content
        for content in runtime.graph.objects(type="acquired_content")
    }

    blocks = contents[items["conversation-content:msg-blocks"].id].data
    assert blocks["normalized_content"] == (
        "Run the report.\n[tool_use content omitted]\n[tool_result content omitted]"
    )
    assert blocks["normalized_metadata"]["content_source"] == "content_blocks"
    assert blocks["normalized_metadata"]["block_count"] == 3

    legacy = contents[items["conversation-content:msg-legacy"].id].data
    assert legacy["normalized_content"] == "Legacy message with only a text field."
    assert legacy["normalized_metadata"]["content_source"] == "text_field"

    empty = contents[items["conversation-content:msg-empty"].id].data
    assert empty["normalized_content"] == ""
    assert empty["normalized_metadata"]["content_source"] == "empty"
    # A message with no timestamps falls back to conversation provider time.
    assert empty["normalized_metadata"]["provider_time"] == (
        "2026-01-05T09:05:00.000000+00:00"
    )


def test_provider_time_normalization(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    export_path = tmp_path / "claude-export.zip"
    conversations = [
        _conversation(
            "conversation-time",
            "Timestamps",
            [
                _message(
                    "msg-iso",
                    "human",
                    "ISO timestamps pass through.",
                    "  2026-01-05T09:00:00.000000+00:00  ",
                ),
                _message(
                    "msg-epoch",
                    "assistant",
                    "Numeric epochs normalize to UTC ISO.",
                    1_704_067_200,
                ),
            ],
        )
    ]
    _write_zip_export(export_path, conversations)

    runtime = _runtime(artifact_dir)
    result = import_claude_export_fn(
        runtime.graph,
        str(export_path),
        "surface_claude_time",
        artifact_store_dir=str(artifact_dir),
    )
    runtime.run_until_idle()

    assert result["ok"] is True
    items = {
        item.data["provider_item_id"]: item
        for item in runtime.graph.objects(type="acquired_item")
    }
    assert items["conversation-time:msg-iso"].data["provider_time"] == (
        "2026-01-05T09:00:00.000000+00:00"
    )
    assert items["conversation-time:msg-epoch"].data["provider_time"] == (
        "2024-01-01T00:00:00+00:00"
    )


def test_malformed_conversation_is_skipped_whole_and_siblings_survive(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    export_path = tmp_path / "claude-export.zip"
    malformed = _conversation(
        "conversation-bad",
        "Broken",
        [
            _message(
                "msg-ok-before-bad",
                "human",
                "This valid message must not be emitted.",
                "2026-01-05T09:00:00.000000+00:00",
            ),
            {"text": "message with no uuid", "sender": "assistant"},
        ],
    )
    _write_zip_export(export_path, [malformed, *_flat_export()])

    runtime = _runtime(artifact_dir)
    result = import_claude_export_fn(
        runtime.graph,
        str(export_path),
        "surface_claude_malformed",
        artifact_store_dir=str(artifact_dir),
    )
    runtime.run_until_idle()

    assert result["ok"] is False
    assert result["imported"] == 2
    assert result["failed"] == 1
    assert result["conversations"] == 1

    items = list(runtime.graph.objects(type="acquired_item"))
    assert sorted(item.data["provider_item_id"] for item in items) == [
        "conversation-flat:msg-assistant",
        "conversation-flat:msg-user",
    ]
    failures = list(runtime.graph.objects(type="ingestion_failure"))
    assert len(failures) == 1
    failure = failures[0].data
    assert failure["stage"] == "acquisition"
    assert failure["error_code"] == "invalid_conversation"
    assert failure["importer_id"] == "claude_export"
    assert failure["metadata"]["conversation_uuid"] == "conversation-bad"
    assert len(list(runtime.graph.objects(type="activity_evidence"))) == 2


def test_conversation_and_message_bounds_stop_without_partial_emission(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    export_path = tmp_path / "claude-export.zip"
    conversations = [
        _conversation(
            "conversation-a",
            "First",
            [
                _message("a-1", "human", "one", "2026-01-05T09:00:00.000000+00:00"),
                _message("a-2", "assistant", "two", "2026-01-05T09:01:00.000000+00:00"),
            ],
        ),
        _conversation(
            "conversation-b",
            "Second",
            [
                _message("b-1", "human", "three", "2026-01-05T09:02:00.000000+00:00"),
            ],
        ),
    ]
    _write_zip_export(export_path, conversations)

    graph = Graph()
    capped = import_claude_export_fn(
        graph,
        str(export_path),
        "surface_claude_conversation_bound",
        artifact_store_dir=str(artifact_dir),
        max_conversations=1,
    )
    assert capped["stopped_at_bound"] is True
    assert capped["imported"] == 2
    codes = [
        obj.data["error_code"] for obj in graph.objects(type="ingestion_failure")
    ]
    assert codes == ["conversation_bound_reached"]

    graph = Graph()
    message_capped = import_claude_export_fn(
        graph,
        str(export_path),
        "surface_claude_message_bound",
        artifact_store_dir=str(artifact_dir),
        max_messages=1,
    )
    assert message_capped["stopped_at_bound"] is True
    # The first conversation would exceed the limit; nothing partial emits.
    assert message_capped["imported"] == 0
    assert not list(graph.objects(type="acquired_item"))
    codes = [
        obj.data["error_code"] for obj in graph.objects(type="ingestion_failure")
    ]
    assert codes == ["message_bound_reached"]


def test_replay_modes_and_artifact_hash_match(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    export_path = tmp_path / "claude-export.zip"
    _write_zip_export(export_path, _flat_export())

    runtime = _runtime(artifact_dir)
    result = import_claude_export_fn(
        runtime.graph,
        str(export_path),
        "surface_claude_replay",
        artifact_store_dir=str(artifact_dir),
        replay_mode="artifact",
    )
    runtime.run_until_idle()

    assert result["ok"] is True
    for item in runtime.graph.objects(type="acquired_item"):
        digest = item.data["replay_payload_hash"]
        assert item.data["replay_payload_ref"] == (
            f"artifact://sha256/{digest[:2]}/{digest}"
        )
        stored = (artifact_dir / "sha256" / digest[:2] / digest).read_bytes()
        assert hashlib.sha256(stored).hexdigest() == digest
        replay_unit = json.loads(stored.decode("utf-8"))
        assert replay_unit["conversation_uuid"] == "conversation-flat"
        assert replay_unit["message_uuid"] in {"msg-user", "msg-assistant"}
        assert isinstance(replay_unit["normalized_content"], str)

    inline_graph = Graph()
    inline = import_claude_export_fn(
        inline_graph,
        str(export_path),
        "surface_claude_inline",
        artifact_store_dir=str(tmp_path / "inline-artifacts"),
        replay_mode="inline",
    )
    assert inline["ok"] is True
    for item in inline_graph.objects(type="acquired_item"):
        payload = item.data["replay_payload_ref"]
        assert hashlib.sha256(payload.encode("utf-8")).hexdigest() == (
            item.data["replay_payload_hash"]
        )
    assert not (tmp_path / "inline-artifacts").exists()

    reference_graph = Graph()
    reference = import_claude_export_fn(
        reference_graph,
        str(export_path),
        "surface_claude_reference",
        artifact_store_dir=str(tmp_path / "reference-artifacts"),
        replay_mode="reference_only",
    )
    assert reference["ok"] is True
    for item in reference_graph.objects(type="acquired_item"):
        assert item.data["replay_payload_ref"] == "reference_only:no-payload"
    assert not (tmp_path / "reference-artifacts").exists()


def test_is_fixture_flag_propagates_to_content_and_evidence(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    export_path = tmp_path / "claude-export.zip"
    _write_zip_export(export_path, _flat_export())

    runtime = _runtime(artifact_dir)
    result = import_claude_export_fn(
        runtime.graph,
        str(export_path),
        "surface_claude_fixture_flag",
        artifact_store_dir=str(artifact_dir),
        is_fixture=True,
    )
    runtime.run_until_idle()

    assert result["ok"] is True
    contents = list(runtime.graph.objects(type="acquired_content"))
    assert contents and all(o.data["is_fixture"] is True for o in contents)
    evidence = list(runtime.graph.objects(type="activity_evidence"))
    assert evidence and all(o.data["is_fixture"] is True for o in evidence)
