"""Bounded-window semantics for the Assistant Local Sessions importer.

Local Claude Code and Codex JSONL logs are unversioned and append-only.  The
importer must window deterministically, skip drifted/malformed lines without
failing sessions, and keep per-line identities stable across re-imports and
appends so the Activity Normalizer creates evidence exactly once per line.

All session content below is synthetic.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from activegraph import Graph, Runtime

sys.path.insert(0, str(Path(__file__).parents[1]))

from packs.activity_normalizer import (
    ActivityNormalizerSettings,
    pack as activity_normalizer_pack,
)
from packs.core import CoreSettings, pack as core_pack
from packs.importers.assistant_local_sessions import (
    AssistantLocalSessionsSettings,
    pack as assistant_local_sessions_pack,
)
from packs.importers.assistant_local_sessions.tools import (
    import_assistant_local_sessions_fn,
)


def _runtime(artifact_dir: Path) -> Runtime:
    runtime = Runtime(Graph())
    runtime.load_pack(core_pack, settings=CoreSettings())
    runtime.load_pack(
        activity_normalizer_pack,
        settings=ActivityNormalizerSettings(artifact_store_dir=str(artifact_dir)),
    )
    runtime.load_pack(
        assistant_local_sessions_pack,
        settings=AssistantLocalSessionsSettings(artifact_store_dir=str(artifact_dir)),
    )
    return runtime


def _write_jsonl(path: Path, lines: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = [
        line if isinstance(line, str) else json.dumps(line, sort_keys=True)
        for line in lines
    ]
    path.write_text("\n".join(rendered) + "\n", encoding="utf-8")


def _set_mtime(path: Path, seconds: int) -> None:
    os.utime(path, ns=(seconds * 1_000_000_000, seconds * 1_000_000_000))


def _claude_line(role: str, content, uuid: str, session_id: str, ts: str) -> dict:
    return {
        "type": role,
        "message": {"role": role, "content": content},
        "uuid": uuid,
        "timestamp": ts,
        "sessionId": session_id,
        "cwd": "/Users/casey/code/demo",
    }


def _claude_session(session_id: str, ts: str) -> list:
    return [
        {"type": "summary", "summary": "A synthetic summary", "leafUuid": "leaf-1"},
        _claude_line("user", "Rename the widget module.", f"{session_id}-u1", session_id, ts),
        _claude_line(
            "assistant",
            [
                {"type": "text", "text": "Renaming it now."},
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "mv a b"}},
            ],
            f"{session_id}-a1",
            session_id,
            ts,
        ),
        _claude_line(
            "user",
            [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
            f"{session_id}-tr1",
            session_id,
            ts,
        ),
    ]


def _build_claude_root(root: Path) -> Path:
    project = root / "-Users-casey-code-demo"
    for session_id, ts, mtime in (
        ("sess-old", "2026-07-01T10:00:00.000Z", 1_000),
        ("sess-mid", "2026-07-02T10:00:00.000Z", 2_000),
        ("sess-new", "2026-07-03T10:00:00.000Z", 3_000),
    ):
        path = project / f"{session_id}.jsonl"
        _write_jsonl(path, _claude_session(session_id, ts))
        _set_mtime(path, mtime)
    return root


def _codex_response(role: str, part_type: str, text: str, ts: str) -> dict:
    return {
        "type": "response_item",
        "timestamp": ts,
        "payload": {
            "type": "message",
            "role": role,
            "content": [{"type": part_type, "text": text}],
        },
    }


def _codex_session(session_id: str, ts: str) -> list:
    return [
        {
            "type": "session_meta",
            "timestamp": ts,
            "payload": {"id": session_id, "cwd": "/Users/casey/code/demo"},
        },
        _codex_response("developer", "input_text", "Synthetic system prompt.", ts),
        _codex_response("user", "input_text", "Summarize the release notes.", ts),
        {"type": "event_msg", "timestamp": ts, "payload": {"type": "token_count", "total": 9}},
        _codex_response("assistant", "output_text", "The release adds an importer.", ts),
        {
            "type": "message",
            "role": "user",
            "timestamp": ts,
            "content": [{"type": "input_text", "text": "Older flat variant line."}],
        },
    ]


def test_claude_code_messages_become_evidence(tmp_path: Path) -> None:
    root = _build_claude_root(tmp_path / "projects")
    artifact_dir = tmp_path / "artifacts"
    runtime = _runtime(artifact_dir)

    result = import_assistant_local_sessions_fn(
        runtime.graph,
        str(root),
        "surface_claude_local",
        provider="claude_code",
        artifact_store_dir=str(artifact_dir),
    )
    runtime.run_until_idle()

    assert result["ok"] is True
    assert result["provider"] == "claude_code"
    assert result["imported"] == 6  # 2 text messages x 3 sessions
    assert result["failed"] == 0
    assert result["skipped_lines"] == 6  # summary + tool_result-only user line x 3

    items = {
        item.data["dedup_key"]: item
        for item in runtime.graph.objects(type="acquired_item")
    }
    assert "sess-new:sess-new-u1" in items
    assert "sess-new:sess-new-a1" in items
    for item in items.values():
        assert item.data["provider_item_id"] == item.data["dedup_key"]
        assert item.data["media_type"] == "application/json"
        assert item.data["importer_id"] == "assistant_local_sessions"
        assert item.data["importer_version"] == "0.1.0"
        assert item.data["provider_time"] is not None

    evidence = list(runtime.graph.objects(type="activity_evidence"))
    assert len(evidence) == 6
    by_key = {e.data["dedup_key"]: e for e in evidence}
    meta = by_key["sess-new:sess-new-a1"].data["normalized_metadata"]
    assert meta["role"] == "assistant"
    assert meta["session_id"] == "sess-new"
    assert meta["cwd"] == "/Users/casey/code/demo"
    assert by_key["sess-new:sess-new-a1"].data["normalized_content"] == "Renaming it now."
    assert all(e.data["source_category"] == "ai_activity" for e in evidence)
    assert all(e.data["connection_path"] == "local" for e in evidence)

    cursors = list(runtime.graph.objects(type="backfill_cursor"))
    assert len(cursors) == 1
    assert cursors[0].data["oldest_ingested_ref"] == "-Users-casey-code-demo/sess-mid.jsonl"
    assert cursors[0].data["newest_ingested_ref"] == "-Users-casey-code-demo/sess-old.jsonl"


def test_codex_variants_session_meta_identity_and_filename_window(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    old = root / "2026/07/01/rollout-2026-07-01T09-00-00-aaaa1111.jsonl"
    new = root / "2026/07/02/rollout-2026-07-02T09-00-00-bbbb2222.jsonl"
    _write_jsonl(old, _codex_session("codex-old", "2026-07-01T09:00:00Z"))
    _write_jsonl(new, _codex_session("codex-new", "2026-07-02T09:00:00Z"))
    # Invert mtimes: the window key for Codex is the filename timestamp,
    # so the newer *filename* must win even with an older mtime.
    _set_mtime(new, 1_000)
    _set_mtime(old, 2_000)
    # A stray non-rollout jsonl is never considered a Codex session.
    _write_jsonl(root / "2026/07/02/notes.jsonl", [{"type": "message", "role": "user", "content": "x"}])

    artifact_dir = tmp_path / "artifacts"
    runtime = _runtime(artifact_dir)
    result = import_assistant_local_sessions_fn(
        runtime.graph,
        str(root),
        "surface_codex_local",
        provider="codex",
        artifact_store_dir=str(artifact_dir),
        max_sessions=1,
    )
    runtime.run_until_idle()

    assert result["ok"] is True
    assert result["imported"] == 3  # user + assistant + flat variant
    window = result["window"]
    assert window["order_key"] == "filename_timestamp"
    assert window["files_considered"] == 2
    assert window["files_skipped_by_window"] == 1
    assert window["files_imported"] == [
        "2026/07/02/rollout-2026-07-02T09-00-00-bbbb2222.jsonl"
    ]

    keys = sorted(
        item.data["dedup_key"] for item in runtime.graph.objects(type="acquired_item")
    )
    # session_meta id owns identity; uuid-less lines fall back to line numbers.
    assert keys == ["codex-new:line-3", "codex-new:line-5", "codex-new:line-6"]
    evidence = list(runtime.graph.objects(type="activity_evidence"))
    assert len(evidence) == 3
    roles = sorted(e.data["normalized_metadata"]["role"] for e in evidence)
    assert roles == ["assistant", "user", "user"]
    assert all(
        e.data["normalized_metadata"]["filename_timestamp"] == "2026-07-02T09-00-00"
        for e in evidence
    )


def test_window_default_bound_and_explicit_extension(tmp_path: Path) -> None:
    root = _build_claude_root(tmp_path / "projects")
    artifact_dir = tmp_path / "artifacts"

    narrow = _runtime(artifact_dir)
    result = import_assistant_local_sessions_fn(
        narrow.graph,
        str(root),
        "surface_window",
        provider="claude_code",
        artifact_store_dir=str(artifact_dir),
        max_sessions=1,
    )
    assert result["imported"] == 2
    window = result["window"]
    assert window["max_sessions"] == 1
    assert window["files_considered"] == 3
    assert window["files_skipped_by_window"] == 2
    assert window["files_imported"] == ["-Users-casey-code-demo/sess-new.jsonl"]
    assert window["files_selected"][0]["ref"] == "-Users-casey-code-demo/sess-new.jsonl"
    assert window["files_selected"][0]["file_mtime_ns"] == 3_000 * 1_000_000_000

    wide = _runtime(artifact_dir)
    extended = import_assistant_local_sessions_fn(
        wide.graph,
        str(root),
        "surface_window",
        provider="claude_code",
        artifact_store_dir=str(artifact_dir),
        max_sessions=10,
    )
    assert extended["imported"] == 6
    assert extended["window"]["files_skipped_by_window"] == 0
    assert len(extended["window"]["files_imported"]) == 3


def test_reimport_is_stable_and_append_adds_only_new_lines(tmp_path: Path) -> None:
    root = _build_claude_root(tmp_path / "projects")
    artifact_dir = tmp_path / "artifacts"
    runtime = _runtime(artifact_dir)

    def _import() -> dict:
        result = import_assistant_local_sessions_fn(
            runtime.graph,
            str(root),
            "surface_reimport",
            provider="claude_code",
            artifact_store_dir=str(artifact_dir),
        )
        runtime.run_until_idle()
        return result

    first = _import()
    assert first["imported"] == 6
    evidence = list(runtime.graph.objects(type="activity_evidence"))
    assert len(evidence) == 6

    # Identical re-import re-emits acquired records but creates no new
    # evidence revisions: the normalizer owns dedup on stable identities.
    second = _import()
    assert second["imported"] == 6
    evidence = list(runtime.graph.objects(type="activity_evidence"))
    assert len(evidence) == 6
    assert all(e.data["revision_number"] == 1 for e in evidence)
    assert all(e.data["status"] == "current" for e in evidence)

    # Appending to a session (jsonl is append-only) creates evidence only
    # for the new line; prior line identities and hashes are untouched.
    target = root / "-Users-casey-code-demo" / "sess-new.jsonl"
    appended = _claude_line(
        "assistant",
        [{"type": "text", "text": "A brand new appended reply."}],
        "sess-new-a9",
        "sess-new",
        "2026-07-04T10:00:00.000Z",
    )
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(appended, sort_keys=True) + "\n")
    _set_mtime(target, 4_000)

    third = _import()
    assert third["imported"] == 7
    evidence = list(runtime.graph.objects(type="activity_evidence"))
    assert len(evidence) == 7
    assert all(e.data["revision_number"] == 1 for e in evidence)
    new = [e for e in evidence if e.data["dedup_key"] == "sess-new:sess-new-a9"]
    assert len(new) == 1
    assert new[0].data["normalized_content"] == "A brand new appended reply."


def test_malformed_lines_and_unreadable_files_are_tolerated(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project = root / "-Users-casey-code-demo"
    _write_jsonl(
        project / "sess-mixed.jsonl",
        [
            "{not json at all",
            {"type": "user", "message": {"role": "user", "content": "Still imported."},
             "uuid": "u-1", "sessionId": "sess-mixed", "timestamp": "2026-07-03T10:00:00Z"},
            '"a bare json string line"',
            {"type": "ai-title", "aiTitle": "Synthetic title"},
        ],
    )
    (project / "sess-binary.jsonl").write_bytes(b"\xff\xfe\x80 not utf-8")

    artifact_dir = tmp_path / "artifacts"
    runtime = _runtime(artifact_dir)
    result = import_assistant_local_sessions_fn(
        runtime.graph,
        str(root),
        "surface_tolerance",
        provider="claude_code",
        artifact_store_dir=str(artifact_dir),
    )
    runtime.run_until_idle()

    assert result["ok"] is False
    assert result["imported"] == 1
    assert result["failed"] == 1
    assert result["malformed_lines"] == 2
    assert result["skipped_lines"] == 1
    assert result["window"]["files_failed"] == ["-Users-casey-code-demo/sess-binary.jsonl"]

    failures = list(runtime.graph.objects(type="ingestion_failure"))
    assert len(failures) == 1
    assert failures[0].data["stage"] == "acquisition"
    assert failures[0].data["error_code"] == "unreadable_file"
    assert failures[0].data["recoverable"] is True
    assert failures[0].data["importer_id"] == "assistant_local_sessions"

    evidence = list(runtime.graph.objects(type="activity_evidence"))
    assert len(evidence) == 1
    assert evidence[0].data["normalized_content"] == "Still imported."


def test_replay_artifact_holds_canonical_message_unit(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    session_line = _claude_line(
        "user", "Verify replay please.", "u-replay", "sess-replay", "2026-07-03T10:00:00Z"
    )
    _write_jsonl(root / "-Users-casey-code-demo" / "sess-replay.jsonl", [session_line])
    artifact_dir = tmp_path / "artifacts"
    runtime = _runtime(artifact_dir)

    result = import_assistant_local_sessions_fn(
        runtime.graph,
        str(root),
        "surface_replay",
        provider="claude_code",
        artifact_store_dir=str(artifact_dir),
    )
    assert result["imported"] == 1

    item = list(runtime.graph.objects(type="acquired_item"))[0]
    digest = item.data["replay_payload_hash"]
    assert item.data["replay_mode"] == "artifact"
    assert item.data["replay_payload_ref"] == f"artifact://sha256/{digest[:2]}/{digest}"
    artifact = artifact_dir / "sha256" / digest[:2] / digest
    assert artifact.is_file()
    payload = artifact.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == digest

    unit = json.loads(payload.decode("utf-8"))
    assert unit["provider"] == "claude_code"
    assert unit["session_id"] == "sess-replay"
    assert unit["message_uuid"] == "u-replay"
    assert unit["role"] == "user"
    assert unit["line_number"] == 1
    assert unit["normalized_content"] == "Verify replay please."
    # Canonical JSON: byte-for-byte reproducible from the parsed unit.
    canonical = json.dumps(
        unit, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert canonical == payload

    raw_line = (root / "-Users-casey-code-demo" / "sess-replay.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()[0]
    assert item.data["source_hash"] == hashlib.sha256(raw_line.encode("utf-8")).hexdigest()


def test_is_fixture_flag_propagates(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    _write_jsonl(
        root / "-Users-casey-code-demo" / "sess-flag.jsonl",
        [_claude_line("user", "Flag me.", "u-flag", "sess-flag", "2026-07-03T10:00:00Z")],
    )
    artifact_dir = tmp_path / "artifacts"
    runtime = _runtime(artifact_dir)

    import_assistant_local_sessions_fn(
        runtime.graph,
        str(root),
        "surface_flag",
        provider="claude_code",
        artifact_store_dir=str(artifact_dir),
        is_fixture=True,
    )
    runtime.run_until_idle()

    content = list(runtime.graph.objects(type="acquired_content"))[0]
    assert content.data["is_fixture"] is True
    evidence = list(runtime.graph.objects(type="activity_evidence"))[0]
    assert evidence.data["is_fixture"] is True


def test_usage_errors_raise_before_any_graph_writes(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "artifacts")
    for kwargs in (
        {"provider": "cursor"},
        {"replay_mode": "verbatim"},
        {"max_sessions": 0},
    ):
        try:
            import_assistant_local_sessions_fn(
                runtime.graph,
                str(tmp_path),
                "surface_usage",
                **kwargs,
            )
        except ValueError:
            pass
        else:  # pragma: no cover - defensive
            raise AssertionError(f"expected ValueError for {kwargs}")
    try:
        import_assistant_local_sessions_fn(runtime.graph, str(tmp_path), "")
    except ValueError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ValueError for empty surface")
    assert not list(runtime.graph.objects(type="acquired_item"))
    assert not list(runtime.graph.objects(type="ingestion_failure"))
