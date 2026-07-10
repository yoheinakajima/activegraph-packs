"""Deterministic, offline fixtures for the Assistant Local Sessions importer.

All session trees are fully synthetic — no real session content is ever
copied here.  Both provider layouts, the bounded window, malformed-line
tolerance, and unreadable-file failure recording are exercised end to end
through the Activity Normalizer.
"""

from __future__ import annotations

import json
import os
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
from packs.importers.assistant_local_sessions import pack as assistant_local_sessions_pack
from packs.importers.assistant_local_sessions.tools import (
    import_assistant_local_sessions_fn,
)


def _runtime(artifact_store: Path) -> tuple[Graph, Runtime]:
    graph = Graph()
    runtime = Runtime(graph)
    runtime.load_pack(core_pack)
    runtime.load_pack(
        activity_normalizer_pack,
        settings=ActivityNormalizerSettings(artifact_store_dir=str(artifact_store)),
    )
    runtime.load_pack(assistant_local_sessions_pack)
    return graph, runtime


def _write_jsonl(path: Path, lines: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = []
    for line in lines:
        rendered.append(line if isinstance(line, str) else json.dumps(line, sort_keys=True))
    path.write_text("\n".join(rendered) + "\n", encoding="utf-8")


def _claude_line(role: str, content, uuid: str, session_id: str, ts: str) -> dict:
    return {
        "type": role,
        "message": {"role": role, "content": content},
        "uuid": uuid,
        "timestamp": ts,
        "sessionId": session_id,
        "cwd": "/Users/casey/code/demo",
    }


def _claude_session_lines(session_id: str, base_ts: str) -> list:
    return [
        {"type": "summary", "summary": "Synthetic session summary", "leafUuid": "u-x"},
        _claude_line("user", "Please rename the widget module.", f"{session_id}-u1", session_id, base_ts),
        _claude_line(
            "assistant",
            [
                {"type": "text", "text": "Renaming the widget module now."},
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "mv a b"}},
            ],
            f"{session_id}-a1",
            session_id,
            base_ts,
        ),
        _claude_line(
            "user",
            [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
            f"{session_id}-tr1",
            session_id,
            base_ts,
        ),
        "{this line is not JSON",
        {"type": "system", "subtype": "hook", "content": "hook ran"},
        _claude_line(
            "assistant",
            [{"type": "text", "text": "Done. The module was renamed."}],
            f"{session_id}-a2",
            session_id,
            base_ts,
        ),
    ]


def _build_claude_tree(root: Path) -> None:
    project = root / "-Users-casey-code-demo"
    sessions = [
        ("sess-old", "2026-07-01T10:00:00.000Z", 1_000),
        ("sess-mid", "2026-07-02T10:00:00.000Z", 2_000),
        ("sess-new", "2026-07-03T10:00:00.000Z", 3_000),
    ]
    for session_id, ts, mtime in sessions:
        path = project / f"{session_id}.jsonl"
        _write_jsonl(path, _claude_session_lines(session_id, ts))
        os.utime(path, ns=(mtime * 1_000_000_000, mtime * 1_000_000_000))


def _codex_response(role: str, part_type: str, text: str, ts: str) -> dict:
    return {
        "type": "response_item",
        "timestamp": ts,
        "payload": {"type": "message", "role": role, "content": [{"type": part_type, "text": text}]},
    }


def _codex_session_lines(session_id: str, ts: str) -> list:
    return [
        {
            "type": "session_meta",
            "timestamp": ts,
            "payload": {"id": session_id, "cwd": "/Users/casey/code/demo", "cli_version": "9.9.9"},
        },
        _codex_response("developer", "input_text", "You are a synthetic system prompt.", ts),
        _codex_response("user", "input_text", "Summarize the release notes.", ts),
        {"type": "event_msg", "timestamp": ts, "payload": {"type": "token_count", "total": 12}},
        _codex_response("assistant", "output_text", "The release adds a bounded importer.", ts),
        "%% malformed line %%",
        {
            "type": "message",
            "role": "user",
            "timestamp": ts,
            "content": [{"type": "input_text", "text": "Older flat variant line."}],
        },
    ]


def _build_codex_tree(root: Path) -> None:
    days = [
        ("2026/07/01", "rollout-2026-07-01T09-00-00-aaaa1111", "codex-old", "2026-07-01T09:00:00Z"),
        ("2026/07/02", "rollout-2026-07-02T09-00-00-bbbb2222", "codex-mid", "2026-07-02T09:00:00Z"),
        ("2026/07/03", "rollout-2026-07-03T09-00-00-cccc3333", "codex-new", "2026-07-03T09:00:00Z"),
    ]
    for day, stem, session_id, ts in days:
        _write_jsonl(root / day / f"{stem}.jsonl", _codex_session_lines(session_id, ts))


def run_claude_code_window_fixture(tmp: Path) -> dict:
    """Claude layout: window keeps the 2 newest of 3 sessions; lines skip cleanly."""
    root = tmp / "claude-projects"
    _build_claude_tree(root)
    store = tmp / "replay-claude"
    graph, runtime = _runtime(store)
    result = import_assistant_local_sessions_fn(
        graph,
        str(root),
        "surface_claude_fixture",
        provider="claude_code",
        artifact_store_dir=str(store),
        max_sessions=2,
        is_fixture=True,
    )
    runtime.run_until_idle()

    assert result["ok"] is True, result
    # 3 text messages per session x 2 windowed sessions
    assert result["imported"] == 6
    assert result["sessions_imported"] == 2
    assert result["malformed_lines"] == 2
    assert result["skipped_lines"] >= 4  # summary/system/tool_result lines per session
    window = result["window"]
    assert window["files_considered"] == 3
    assert window["files_skipped_by_window"] == 1
    assert window["order_key"] == "file_mtime_then_name"
    assert sorted(window["files_imported"]) == [
        "-Users-casey-code-demo/sess-mid.jsonl",
        "-Users-casey-code-demo/sess-new.jsonl",
    ]

    items = list(graph.objects(type="acquired_item"))
    assert len(items) == 6
    keys = {item.data["dedup_key"] for item in items}
    assert "sess-new:sess-new-u1" in keys
    assert all(item.data["media_type"] == "application/json" for item in items)
    assert all(
        item.data["replay_payload_ref"].startswith("artifact://sha256/") for item in items
    )
    contents = list(graph.objects(type="acquired_content"))
    assert all(o.data["source_category"] == "ai_activity" for o in contents)
    assert all(o.data["connection_path"] == "local" for o in contents)
    assert all(o.data["is_fixture"] is True for o in contents)

    evidence = list(graph.objects(type="activity_evidence"))
    assert len(evidence) == 6
    cursors = list(graph.objects(type="backfill_cursor"))
    assert len(cursors) == 1
    assert cursors[0].data["oldest_ingested_ref"] == "-Users-casey-code-demo/sess-mid.jsonl"
    assert cursors[0].data["newest_ingested_ref"] == "-Users-casey-code-demo/sess-new.jsonl"
    return {"imported": 6, "windowed_out": 1, "evidence": 6}


def run_codex_fixture(tmp: Path) -> dict:
    """Codex layout: nested + flat variants parse; developer/system lines skip."""
    root = tmp / "codex-sessions"
    _build_codex_tree(root)
    store = tmp / "replay-codex"
    graph, runtime = _runtime(store)
    result = import_assistant_local_sessions_fn(
        graph,
        str(root),
        "surface_codex_fixture",
        provider="codex",
        artifact_store_dir=str(store),
        max_sessions=2,
        is_fixture=True,
    )
    runtime.run_until_idle()

    assert result["ok"] is True, result
    # user + assistant + flat-variant user per session x 2 windowed sessions
    assert result["imported"] == 6
    assert result["malformed_lines"] == 2
    window = result["window"]
    assert window["files_considered"] == 3
    assert window["files_skipped_by_window"] == 1
    assert window["order_key"] == "filename_timestamp"

    items = list(graph.objects(type="acquired_item"))
    keys = sorted(item.data["dedup_key"] for item in items)
    # session_meta id owns identity; uuid-less lines use stable line numbers.
    assert "codex-new:line-3" in keys and "codex-new:line-5" in keys and "codex-new:line-7" in keys
    assert all(item.data["provider_time"] is not None for item in items)
    evidence = list(graph.objects(type="activity_evidence"))
    assert len(evidence) == 6
    roles = {e.data["normalized_metadata"]["role"] for e in evidence}
    assert roles == {"user", "assistant"}
    return {"imported": 6, "windowed_out": 1, "evidence": 6}


def run_unreadable_file_fixture(tmp: Path) -> dict:
    """A non-UTF-8 session file records a recoverable failure; siblings commit."""
    root = tmp / "claude-broken"
    project = root / "-Users-casey-code-demo"
    good = project / "sess-good.jsonl"
    _write_jsonl(good, _claude_session_lines("sess-good", "2026-07-03T10:00:00.000Z"))
    bad = project / "sess-bad.jsonl"
    bad.write_bytes(b"\xff\xfe\x00 not utf-8 jsonl \x80")
    store = tmp / "replay-broken"
    graph, runtime = _runtime(store)
    result = import_assistant_local_sessions_fn(
        graph,
        str(root),
        "surface_broken_fixture",
        provider="claude_code",
        artifact_store_dir=str(store),
        is_fixture=True,
    )
    runtime.run_until_idle()

    assert result["ok"] is False
    assert result["imported"] == 3
    assert result["failed"] == 1
    assert result["window"]["files_failed"] == ["-Users-casey-code-demo/sess-bad.jsonl"]
    failures = list(graph.objects(type="ingestion_failure"))
    acquisition = [f for f in failures if f.data["error_code"] == "unreadable_file"]
    assert len(acquisition) == 1
    assert acquisition[0].data["stage"] == "acquisition"
    assert acquisition[0].data["recoverable"] is True
    assert len(list(graph.objects(type="activity_evidence"))) == 3
    return {"imported": 3, "failures": 1}


def run_all() -> bool:
    print("=" * 60)
    print("Assistant Local Sessions Importer Fixtures")
    print("=" * 60)

    scenarios = [
        ("claude code bounded window", run_claude_code_window_fixture),
        ("codex defensive parsing", run_codex_fixture),
        ("unreadable file isolation", run_unreadable_file_fixture),
    ]
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        for label, scenario in scenarios:
            print(f"\n- {label}")
            print(f"  PASS: {scenario(tmp)}")

    print("\nALL PASS")
    return True


if __name__ == "__main__":
    try:
        ok = run_all()
    except AssertionError as exc:
        print(f"\nFAIL: {exc}")
        sys.exit(1)
    sys.exit(0 if ok else 1)
