"""Bounded importer for local agent-session JSONL logs (Claude Code, Codex).

Both layouts are unversioned, append-only JSONL written by tools that change
shape between releases.  The parsers here are therefore deliberately
defensive: a line either yields exactly one user/assistant message unit or it
is skipped and counted; a file either parses within bounds or it records one
``ingestion_failure`` and its siblings continue.  Content problems never
raise out of the tool.

This module owns only the file-format edge.  It never creates evidence,
computes evidence identity, deduplicates, or promotes candidates.
Re-importing intentionally re-emits the same acquired identities again — the
Activity Normalizer, not this importer, owns deduplication and revisioning.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from activegraph.packs import tool


IMPORTER_ID = "assistant_local_sessions"
IMPORTER_VERSION = "0.1.0"
PROVIDERS = frozenset({"claude_code", "codex"})
REPLAY_MODES = frozenset({"inline", "artifact", "reference_only"})
REFERENCE_ONLY_SENTINEL = "reference_only:no-payload"

_MESSAGE_ROLES = frozenset({"user", "assistant"})
_CLAUDE_TEXT_PART_TYPES = frozenset({"text"})
_CODEX_TEXT_PART_TYPES = frozenset({"input_text", "output_text", "text"})
_CODEX_FILENAME_RE = re.compile(
    r"^rollout-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})-(.+)\.jsonl$"
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record_failure(
    graph,
    *,
    source_surface_id: Optional[str],
    source_ref: Optional[str],
    error_code: str,
    message: str,
    recoverable: bool = False,
    metadata: Optional[dict[str, Any]] = None,
) -> str:
    failure = graph.add_object(
        "ingestion_failure",
        {
            "source_surface_id": source_surface_id or None,
            "acquired_item_id": None,
            "source_ref": source_ref or None,
            "stage": "acquisition",
            "error_code": error_code,
            "message": str(message)[:500],
            "importer_id": IMPORTER_ID,
            "importer_version": IMPORTER_VERSION,
            "extractor_id": None,
            "extractor_version": None,
            "recoverable": bool(recoverable),
            "metadata": dict(metadata or {}),
        },
    )
    return failure.id


def _artifact_ref(payload: bytes, artifact_store_dir: str) -> tuple[str, str]:
    """Atomically retain exact replay bytes in the shared v0 CAS layout."""

    digest = _sha256(payload)
    root = Path(artifact_store_dir).expanduser().resolve()
    target = root / "sha256" / digest[:2] / digest
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file() or _sha256(target.read_bytes()) != digest:
            raise OSError(f"artifact collision or corruption at {target}")
    else:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{digest}.", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, target)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
    return f"artifact://sha256/{digest[:2]}/{digest}", digest


def _replay_reference(
    payload: str,
    *,
    replay_mode: str,
    artifact_store_dir: str,
) -> tuple[str, str]:
    encoded = payload.encode("utf-8")
    digest = _sha256(encoded)
    if replay_mode == "inline":
        return payload, digest
    if replay_mode == "reference_only":
        return REFERENCE_ONLY_SENTINEL, digest
    return _artifact_ref(encoded, artifact_store_dir)


def _provider_time(value: Any) -> Optional[str]:
    """Return the line's own timestamp string only when it parses as ISO-8601."""

    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    probe = candidate[:-1] + "+00:00" if candidate.endswith(("Z", "z")) else candidate
    try:
        datetime.fromisoformat(probe)
    except ValueError:
        return None
    return candidate


def _joined_text(content: Any, text_part_types: frozenset[str]) -> tuple[str, int]:
    """Join plain-string and typed text parts; every other part shape is skipped."""

    if isinstance(content, str):
        raw_parts: list[Any] = [content]
    elif isinstance(content, list):
        raw_parts = content
    else:
        raw_parts = []
    texts: list[str] = []
    for part in raw_parts:
        if isinstance(part, str):
            texts.append(part)
        elif isinstance(part, dict):
            part_type = part.get("type")
            if (part_type is None or part_type in text_part_types) and isinstance(
                part.get("text"), str
            ):
                texts.append(part["text"])
    text = "\n".join(piece for piece in texts if piece)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text, len(raw_parts)


def _extract_claude_code(obj: dict[str, Any]) -> tuple[Optional[dict[str, Any]], str]:
    """One Claude Code JSONL line -> message unit, or (None, skip_reason).

    Non-message line types (``summary``, ``system``, ``attachment``, titles,
    queue operations, hooks, ...) and user/assistant lines carrying only
    tool_use/tool_result/thinking parts are skipped, never failed.
    """

    if obj.get("type") not in _MESSAGE_ROLES:
        return None, "non_message"
    message = obj.get("message")
    if not isinstance(message, dict):
        return None, "non_message"
    role = message.get("role")
    if not isinstance(role, str) or not role:
        role = str(obj["type"])
    if role not in _MESSAGE_ROLES:
        return None, "non_message"
    text, part_count = _joined_text(message.get("content"), _CLAUDE_TEXT_PART_TYPES)
    if not text.strip():
        return None, "non_text"
    uuid = obj.get("uuid")
    session_id = obj.get("sessionId")
    cwd = obj.get("cwd")
    return (
        {
            "role": role,
            "text": text,
            "part_count": part_count,
            "message_uuid": uuid if isinstance(uuid, str) and uuid else None,
            "timestamp": obj.get("timestamp"),
            "line_session_id": session_id if isinstance(session_id, str) and session_id else None,
            "cwd": cwd if isinstance(cwd, str) and cwd else None,
        },
        "",
    )


def _extract_codex(obj: dict[str, Any]) -> tuple[Optional[dict[str, Any]], str]:
    """One Codex rollout JSONL line -> message unit, or (None, skip_reason).

    Accepts the nested ``response_item``/``payload.message`` shape and the
    older flat ``{"type": "message", ...}`` variant.  ``session_meta``,
    ``event_msg``, ``turn_context``, tool call records, and non-user/assistant
    roles (``developer``, ``system``) are skipped, never failed.
    """

    record_type = obj.get("type")
    payload: Optional[dict[str, Any]] = None
    if record_type == "response_item":
        candidate = obj.get("payload")
        if isinstance(candidate, dict) and candidate.get("type") == "message":
            payload = candidate
    elif record_type == "message":
        payload = obj
    if payload is None:
        return None, "non_message"
    role = payload.get("role")
    if role not in _MESSAGE_ROLES:
        return None, "non_message"
    text, part_count = _joined_text(payload.get("content"), _CODEX_TEXT_PART_TYPES)
    if not text.strip():
        return None, "non_text"
    uuid = payload.get("id")
    return (
        {
            "role": role,
            "text": text,
            "part_count": part_count,
            "message_uuid": uuid if isinstance(uuid, str) and uuid else None,
            "timestamp": obj.get("timestamp") or payload.get("timestamp"),
            "line_session_id": None,
            "cwd": None,
        },
        "",
    )


def _codex_session_identity(path: Path, lines: list[str]) -> tuple[str, Optional[str]]:
    """Resolve (session_id, session_cwd) from the first session_meta record.

    Falls back to the rollout filename UUID, then the filename stem.  Only the
    first well-formed ``session_meta`` wins so forked/resumed rollouts (which
    append further session_meta records) keep one stable identity.
    """

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "session_meta":
            continue
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        meta_id = payload.get("id") or payload.get("session_id")
        cwd = payload.get("cwd")
        if isinstance(meta_id, str) and meta_id:
            return meta_id, cwd if isinstance(cwd, str) and cwd else None
    match = _CODEX_FILENAME_RE.match(path.name)
    if match:
        return match.group(2), None
    return path.stem, None


def _walk_jsonl_files(root: Path) -> list[Path]:
    """Collect regular non-symlink ``.jsonl`` files without following symlinks."""

    found: list[Path] = []
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        dirs[:] = sorted(d for d in dirs if not (current_path / d).is_symlink())
        for name in sorted(files):
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix.lower() == ".jsonl":
                found.append(path)
    return found


def _discover_windowed_files(
    root: Path, provider: str, max_sessions: int
) -> tuple[list[dict[str, Any]], int, int, str]:
    """Return (selected ascending-order entries, considered, skipped, order key).

    Claude Code files are ordered by file mtime with a relative-filename
    tiebreak; Codex rollouts by the timestamp embedded in the filename.  The
    window keeps the most recent ``max_sessions`` files and is fully recorded
    by the caller in the run's window log.
    """

    entries: list[dict[str, Any]] = []
    if provider == "codex":
        order_key_name = "filename_timestamp"
        for path in _walk_jsonl_files(root):
            match = _CODEX_FILENAME_RE.match(path.name)
            if match is None:
                continue
            ref = path.relative_to(root).as_posix()
            entries.append(
                {
                    "path": path,
                    "ref": ref,
                    "order_key": (match.group(1), ref),
                    "filename_timestamp": match.group(1),
                }
            )
    else:
        order_key_name = "file_mtime_then_name"
        for path in _walk_jsonl_files(root):
            ref = path.relative_to(root).as_posix()
            mtime_ns = path.stat().st_mtime_ns
            entries.append(
                {
                    "path": path,
                    "ref": ref,
                    "order_key": (mtime_ns, ref),
                    "file_mtime_ns": mtime_ns,
                }
            )
    entries.sort(key=lambda entry: entry["order_key"], reverse=True)
    considered = len(entries)
    selected = entries[:max_sessions]
    skipped_by_window = considered - len(selected)
    selected.reverse()  # oldest-first processing keeps cursors monotone
    return selected, considered, skipped_by_window, order_key_name


def _find_cursor(graph, source_surface_id: str):
    for cursor in graph.objects(type="backfill_cursor"):
        if (cursor.data or {}).get("source_surface_id") == source_surface_id:
            return cursor
    return None


def _advance_cursor(graph, cursor, source_surface_id: str, stable_ref: str):
    if cursor is None:
        return graph.add_object(
            "backfill_cursor",
            {
                "source_surface_id": source_surface_id,
                "oldest_ingested_ref": stable_ref,
                "newest_ingested_ref": stable_ref,
                "cursor_version": 1,
            },
        )
    data = cursor.data or {}
    oldest = data.get("oldest_ingested_ref")
    newest = data.get("newest_ingested_ref")
    graph.patch_object(
        cursor.id,
        {
            "oldest_ingested_ref": min(oldest, stable_ref) if oldest else stable_ref,
            "newest_ingested_ref": max(newest, stable_ref) if newest else stable_ref,
            "cursor_version": 1,
        },
    )
    return graph.get_object(cursor.id)


def import_assistant_local_sessions_fn(
    graph,
    root_path: str,
    source_surface_id: str,
    *,
    provider: str = "claude_code",
    artifact_store_dir: str = ".activegraph/replay-artifacts",
    replay_mode: str = "artifact",
    is_fixture: bool = False,
    max_sessions: int = 20,
    max_file_bytes: int = 50_000_000,
    max_lines_per_file: int = 100_000,
    max_messages: int = 25_000,
    max_normalized_chars: int = 16_000,
) -> dict[str, Any]:
    """Acquire a bounded window of local agent-session logs for one provider.

    One call imports one provider layout rooted at ``root_path`` (the
    Claude Code ``projects/`` or Codex ``sessions/`` directory; callers pass
    absolute paths — default roots live in this pack's settings, never here).
    The window keeps the ``max_sessions`` most recent session files under a
    deterministic order key and is fully recorded in the returned window log.

    One acquired_item/acquired_content pair is emitted per user/assistant
    message line.  A malformed line is skipped and counted; a malformed or
    unreadable file records a recoverable ``ingestion_failure`` and its
    siblings continue.  Re-import intentionally re-emits the same acquired
    identities again — the Activity Normalizer owns deduplication,
    evidence identity, and revisioning; jsonl files are append-only, so
    line-number fallbacks stay stable across appends.
    """

    if provider not in PROVIDERS:
        raise ValueError(f"unsupported provider {provider!r}")
    if replay_mode not in REPLAY_MODES:
        raise ValueError(f"unsupported replay_mode {replay_mode!r}")
    if not source_surface_id:
        raise ValueError("source_surface_id is required")
    bounds = (
        max_sessions,
        max_file_bytes,
        max_lines_per_file,
        max_messages,
        max_normalized_chars,
    )
    if any(value < 1 for value in bounds):
        raise ValueError("all import bounds must be positive")

    acquired_item_ids: list[str] = []
    failure_ids: list[str] = []
    malformed_lines = 0
    skipped_lines = 0
    files_imported: list[str] = []
    files_failed: list[str] = []
    stopped_at_bound = False
    cursor = _find_cursor(graph, source_surface_id)

    def _window(considered: int, skipped: int, order_key: str, selected_refs: list[dict]) -> dict:
        return {
            "provider": provider,
            "order_key": order_key,
            "max_sessions": max_sessions,
            "files_considered": considered,
            "files_selected": selected_refs,
            "files_imported": files_imported,
            "files_failed": files_failed,
            "files_skipped_by_window": skipped,
        }

    def _result(window: dict, ok_hint: bool = True) -> dict[str, Any]:
        return {
            "ok": ok_hint and not failure_ids,
            "provider": provider,
            "imported": len(acquired_item_ids),
            "failed": len(failure_ids),
            "sessions_imported": len(files_imported),
            "malformed_lines": malformed_lines,
            "skipped_lines": skipped_lines,
            "window": window,
            "cursor_id": cursor.id if cursor is not None else None,
            "acquired_item_ids": acquired_item_ids,
            "failure_ids": failure_ids,
            "stopped_at_bound": stopped_at_bound,
        }

    root = Path(root_path).expanduser()
    if root.is_symlink() or not root.exists() or not root.is_dir():
        failure_ids.append(
            _record_failure(
                graph,
                source_surface_id=source_surface_id,
                source_ref=str(root),
                error_code="invalid_root",
                message="root_path must be an existing, non-symlink directory",
                recoverable=True,
            )
        )
        return _result(_window(0, 0, "none", []), ok_hint=False)
    root = root.resolve()

    selected, considered, skipped_by_window, order_key_name = _discover_windowed_files(
        root, provider, max_sessions
    )
    selected_refs = [
        {key: value for key, value in entry.items() if key not in ("path", "order_key")}
        for entry in selected
    ]
    window = _window(considered, skipped_by_window, order_key_name, selected_refs)

    for entry in selected:
        path: Path = entry["path"]
        session_ref: str = entry["ref"]
        file_source_ref = str(path)
        try:
            size = path.stat().st_size
            if size > max_file_bytes:
                raise ValueError(f"file is {size} bytes; limit is {max_file_bytes}")
            payload = path.read_bytes()
            if len(payload) > max_file_bytes:
                raise ValueError(
                    f"file grew to {len(payload)} bytes while reading; limit is {max_file_bytes}"
                )
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"file is not valid UTF-8: {exc}") from exc
        except (OSError, ValueError) as exc:
            files_failed.append(session_ref)
            failure_ids.append(
                _record_failure(
                    graph,
                    source_surface_id=source_surface_id,
                    source_ref=file_source_ref,
                    error_code="unreadable_file",
                    message=str(exc),
                    recoverable=True,
                    metadata={"session_ref": session_ref, "provider": provider},
                )
            )
            continue

        lines = text.splitlines()
        if len(lines) > max_lines_per_file:
            failure_ids.append(
                _record_failure(
                    graph,
                    source_surface_id=source_surface_id,
                    source_ref=file_source_ref,
                    error_code="line_bound_reached",
                    message=(
                        f"file has {len(lines)} lines; only the first "
                        f"{max_lines_per_file} were considered"
                    ),
                    recoverable=True,
                    metadata={
                        "session_ref": session_ref,
                        "line_count": len(lines),
                        "limit": max_lines_per_file,
                    },
                )
            )
            lines = lines[:max_lines_per_file]

        if provider == "codex":
            file_session_id, session_cwd = _codex_session_identity(path, lines)
        else:
            file_session_id, session_cwd = path.stem, None

        imported_this_file = 0
        for line_number, raw_line in enumerate(lines, start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            if len(acquired_item_ids) >= max_messages:
                stopped_at_bound = True
                failure_ids.append(
                    _record_failure(
                        graph,
                        source_surface_id=source_surface_id,
                        source_ref=f"{file_source_ref}#L{line_number}",
                        error_code="message_bound_reached",
                        message=f"run reached the message limit {max_messages}",
                        recoverable=True,
                        metadata={"session_ref": session_ref, "limit": max_messages},
                    )
                )
                break
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                malformed_lines += 1
                continue
            if not isinstance(obj, dict):
                malformed_lines += 1
                continue
            if provider == "codex":
                unit, _skip = _extract_codex(obj)
            else:
                unit, _skip = _extract_claude_code(obj)
            if unit is None:
                skipped_lines += 1
                continue

            session_id = unit["line_session_id"] or file_session_id
            if unit["message_uuid"]:
                stable_suffix = unit["message_uuid"]
            else:
                stable_suffix = f"line-{line_number}"
            provider_item_id = f"{session_id}:{stable_suffix}"
            source_ref = f"{file_source_ref}#L{line_number}"
            provider_time = _provider_time(unit["timestamp"])

            content_text = unit["text"]
            original_chars = len(content_text)
            truncated = original_chars > max_normalized_chars
            if truncated:
                content_text = content_text[:max_normalized_chars]

            normalized_metadata: dict[str, Any] = {
                "provider": provider,
                "session_id": session_id,
                "session_ref": session_ref,
                "line_number": line_number,
                "message_uuid": unit["message_uuid"],
                "role": unit["role"],
                "provider_time": provider_time,
                "cwd": unit["cwd"] or session_cwd,
                "part_count": unit["part_count"],
                "normalized_char_count": original_chars,
                "truncated": truncated,
            }
            if "file_mtime_ns" in entry:
                normalized_metadata["file_mtime_ns"] = entry["file_mtime_ns"]
            if "filename_timestamp" in entry:
                normalized_metadata["filename_timestamp"] = entry["filename_timestamp"]

            replay_unit = {
                "provider": provider,
                "session_id": session_id,
                "session_ref": session_ref,
                "line_number": line_number,
                "message_uuid": unit["message_uuid"],
                "role": unit["role"],
                "provider_time": provider_time,
                "normalized_content": content_text,
                "normalized_metadata": normalized_metadata,
            }
            try:
                replay_ref, replay_hash = _replay_reference(
                    _canonical_json(replay_unit),
                    replay_mode=replay_mode,
                    artifact_store_dir=artifact_store_dir,
                )
            except OSError as exc:
                failure_ids.append(
                    _record_failure(
                        graph,
                        source_surface_id=source_surface_id,
                        source_ref=source_ref,
                        error_code="artifact_write_failed",
                        message=str(exc),
                        recoverable=True,
                        metadata={"provider_item_id": provider_item_id},
                    )
                )
                continue

            acquired = graph.add_object(
                "acquired_item",
                {
                    "source_surface_id": source_surface_id,
                    "provider_item_id": provider_item_id,
                    "dedup_key": provider_item_id,
                    "source_ref": source_ref,
                    "source_hash": _sha256(raw_line.encode("utf-8")),
                    "provider_time": provider_time,
                    "replay_mode": replay_mode,
                    "replay_payload_ref": replay_ref,
                    "replay_payload_hash": replay_hash,
                    "media_type": "application/json",
                    "importer_id": IMPORTER_ID,
                    "importer_version": IMPORTER_VERSION,
                },
            )
            graph.add_object(
                "acquired_content",
                {
                    "acquired_item_id": acquired.id,
                    "normalized_content": content_text,
                    "normalized_metadata": normalized_metadata,
                    "source_category": "ai_activity",
                    "connection_path": "local",
                    "is_fixture": bool(is_fixture),
                },
            )
            acquired_item_ids.append(acquired.id)
            imported_this_file += 1

        if imported_this_file:
            files_imported.append(session_ref)
            cursor = _advance_cursor(graph, cursor, source_surface_id, session_ref)
        if stopped_at_bound:
            break

    return _result(window)


@tool(
    name="import_assistant_local_sessions",
    description=(
        "Acquire a bounded window of the most recent local agent-session JSONL "
        "logs (Claude Code projects/ or Codex sessions/). Emits Activity "
        "Normalizer acquired-item/content records per user/assistant message "
        "line; it does not deduplicate, extract candidates, or promote state."
    ),
)
def import_assistant_local_sessions(
    graph,
    root_path: str = "",
    source_surface_id: str = "",
    provider: str = "claude_code",
    artifact_store_dir: str = ".activegraph/replay-artifacts",
    replay_mode: str = "artifact",
    is_fixture: bool = False,
    max_sessions: int = 20,
    max_file_bytes: int = 50_000_000,
    max_lines_per_file: int = 100_000,
    max_messages: int = 25_000,
    max_normalized_chars: int = 16_000,
) -> dict[str, Any]:
    """Registered capability wrapper for :func:`import_assistant_local_sessions_fn`."""

    return import_assistant_local_sessions_fn(
        graph,
        root_path,
        source_surface_id,
        provider=provider,
        artifact_store_dir=artifact_store_dir,
        replay_mode=replay_mode,
        is_fixture=is_fixture,
        max_sessions=max_sessions,
        max_file_bytes=max_file_bytes,
        max_lines_per_file=max_lines_per_file,
        max_messages=max_messages,
        max_normalized_chars=max_normalized_chars,
    )


TOOLS = [import_assistant_local_sessions]

__all__ = [
    "IMPORTER_ID",
    "IMPORTER_VERSION",
    "PROVIDERS",
    "import_assistant_local_sessions_fn",
    "import_assistant_local_sessions",
    "TOOLS",
]
