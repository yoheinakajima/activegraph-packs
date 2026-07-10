"""Bounded parser for official Claude data-export snapshots.

``conversations.json`` is a flat list of conversations, each carrying an
ordered ``chat_messages`` transcript — unlike the ChatGPT export there is no
branch tree.  Every message becomes one acquired-item/content pair.  The
export arrives either as the official ZIP archive or as a bare
``conversations.json`` file (users often unzip first); both are accepted and
detected by extension and magic bytes.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from activegraph.packs import tool


IMPORTER_ID = "claude_export"
IMPORTER_VERSION = "0.1.0"
CONVERSATIONS_MEMBER = "conversations.json"
ZIP_MAGIC = b"PK\x03\x04"
REPLAY_MODES = frozenset({"inline", "artifact", "reference_only"})
REFERENCE_ONLY_SENTINEL = "reference_only:no-payload"


class ExportFormatError(ValueError):
    """A bounded, user-visible export parsing failure."""


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
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return None
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _block_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if block is None:
        return ""
    if isinstance(block, (int, float, bool)):
        return str(block)
    if not isinstance(block, dict):
        return ""
    block_type = block.get("type")
    if (block_type is None or block_type == "text") and isinstance(block.get("text"), str):
        return block["text"]
    kind = block_type if isinstance(block_type, str) and block_type else "non_text"
    return f"[{kind} content omitted]"


def _normalized_message(message: dict[str, Any], max_chars: int) -> tuple[str, dict[str, Any]]:
    """Prefer typed content blocks; fall back to the legacy plain ``text``."""

    content = message.get("content")
    blocks: list[Any] = []
    if isinstance(content, list):
        blocks = content
        content_source = "content_blocks"
    elif isinstance(content, str):
        blocks = [content]
        content_source = "content_blocks"
    elif content is not None:
        blocks = [content]
        content_source = "content_blocks"
    elif isinstance(message.get("text"), str):
        blocks = [message["text"]]
        content_source = "text_field"
    else:
        content_source = "empty"

    text = "\n".join(filter(None, (_block_text(block) for block in blocks)))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    original_chars = len(text)
    truncated = original_chars > max_chars
    if truncated:
        text = text[:max_chars]
    return text, {
        "content_source": content_source,
        "block_count": len(blocks),
        "normalized_char_count": original_chars,
        "truncated": truncated,
    }


def _conversation_records(
    conversation: Any,
    *,
    max_messages_per_conversation: int,
    max_normalized_chars: int,
) -> list[dict[str, Any]]:
    """Validate one whole conversation, then build all of its message records.

    Any structural problem raises before a single record is returned, so a
    malformed conversation never partially emits.
    """

    if not isinstance(conversation, dict):
        raise ExportFormatError("conversation entry is not an object")
    conversation_uuid = conversation.get("uuid")
    if not isinstance(conversation_uuid, str) or not conversation_uuid:
        raise ExportFormatError("conversation has no stable uuid")
    messages = conversation.get("chat_messages")
    if messages is None:
        messages = []
    if not isinstance(messages, list):
        raise ExportFormatError(
            f"conversation {conversation_uuid!r} chat_messages is not a list"
        )
    if len(messages) > max_messages_per_conversation:
        raise ExportFormatError(
            f"conversation {conversation_uuid!r} has {len(messages)} messages; "
            f"limit is {max_messages_per_conversation}"
        )

    name = conversation.get("name")
    seen_uuids: set[str] = set()
    records: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ExportFormatError(
                f"conversation {conversation_uuid!r} message at index {index} is not an object"
            )
        message_uuid = message.get("uuid")
        if not isinstance(message_uuid, str) or not message_uuid:
            raise ExportFormatError(
                f"conversation {conversation_uuid!r} message at index {index} has no stable uuid"
            )
        if message_uuid in seen_uuids:
            raise ExportFormatError(
                f"conversation {conversation_uuid!r} has duplicate message uuid {message_uuid!r}"
            )
        seen_uuids.add(message_uuid)

        text, content_meta = _normalized_message(message, max_normalized_chars)
        sender = message.get("sender")
        sender = str(sender) if sender is not None else None
        role = {"human": "user", "assistant": "assistant"}.get(sender or "", sender)
        provider_time = _provider_time(
            message.get("created_at")
            or message.get("updated_at")
            or conversation.get("updated_at")
            or conversation.get("created_at")
        )

        normalized_metadata = {
            "conversation_uuid": conversation_uuid,
            "conversation_name": str(name) if name is not None else None,
            "message_uuid": message_uuid,
            "message_index": index,
            "sender": sender,
            "role": role,
            "provider_time": provider_time,
            **content_meta,
        }
        replay_unit = {
            "conversation_uuid": conversation_uuid,
            "conversation_name": str(name) if name is not None else None,
            "message_uuid": message_uuid,
            "message_index": index,
            "sender": sender,
            "provider_time": provider_time,
            "normalized_content": text,
            "normalized_metadata": normalized_metadata,
        }
        records.append(
            {
                "conversation_uuid": conversation_uuid,
                "message_uuid": message_uuid,
                "dedup_key": f"{conversation_uuid}:{message_uuid}",
                "provider_item_id": f"{conversation_uuid}:{message_uuid}",
                "provider_time": provider_time,
                "source_hash": _sha256(_canonical_json(message).encode("utf-8")),
                "normalized_content": text,
                "normalized_metadata": normalized_metadata,
                "replay_payload": _canonical_json(replay_unit),
                "sender": sender,
            }
        )
    return records


def _read_conversations(
    export_path: str,
    *,
    max_archive_bytes: int,
    max_json_bytes: int,
    max_compression_ratio: int,
) -> tuple[Path, str, list[Any]]:
    """Return the resolved path, input kind (``zip``/``json``), and root list."""

    source = Path(export_path).expanduser()
    if source.is_symlink() or not source.is_file():
        raise ExportFormatError(
            "export_path must be an existing, non-symlink ZIP or conversations.json file"
        )
    if source.stat().st_size > max_archive_bytes:
        raise ExportFormatError(
            f"export is {source.stat().st_size} bytes; limit is {max_archive_bytes}"
        )
    source = source.resolve()
    with source.open("rb") as probe:
        magic = probe.read(len(ZIP_MAGIC))
    is_zip = source.suffix.lower() == ".zip" or magic == ZIP_MAGIC

    if is_zip:
        try:
            with zipfile.ZipFile(source, "r") as bundle:
                try:
                    info = bundle.getinfo(CONVERSATIONS_MEMBER)
                except KeyError as exc:
                    raise ExportFormatError("archive has no root conversations.json") from exc
                if info.flag_bits & 0x1:
                    raise ExportFormatError("encrypted conversations.json is unsupported")
                if info.file_size > max_json_bytes:
                    raise ExportFormatError(
                        f"conversations.json is {info.file_size} bytes; limit is {max_json_bytes}"
                    )
                if info.file_size and info.file_size / max(1, info.compress_size) > max_compression_ratio:
                    raise ExportFormatError("conversations.json exceeds compression-ratio bound")
                with bundle.open(info, "r") as handle:
                    raw = handle.read(max_json_bytes + 1)
                if len(raw) > max_json_bytes:
                    raise ExportFormatError("conversations.json exceeded size bound while reading")
        except zipfile.BadZipFile as exc:
            raise ExportFormatError("archive is not a valid ZIP file") from exc
        kind = "zip"
    else:
        if source.stat().st_size > max_json_bytes:
            raise ExportFormatError(
                f"conversations.json is {source.stat().st_size} bytes; limit is {max_json_bytes}"
            )
        raw = source.read_bytes()
        kind = "json"

    try:
        parsed = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportFormatError(f"malformed conversations.json: {exc}") from exc
    if not isinstance(parsed, list):
        raise ExportFormatError("conversations.json root must be a list")
    return source, kind, parsed


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


def import_claude_export_fn(
    graph,
    export_path: str,
    source_surface_id: str,
    *,
    artifact_store_dir: str = ".activegraph/replay-artifacts",
    replay_mode: str = "artifact",
    is_fixture: bool = False,
    max_archive_bytes: int = 1_000_000_000,
    max_conversations_json_bytes: int = 256_000_000,
    max_compression_ratio: int = 200,
    max_conversations: int = 10_000,
    max_messages_per_conversation: int = 10_000,
    max_messages: int = 250_000,
    max_normalized_chars: int = 32_000,
) -> dict[str, Any]:
    """Acquire one bounded official Claude export snapshot (ZIP or bare JSON).

    A malformed conversation is validated before any of its message objects
    are emitted. Valid sibling conversations remain committed. Re-import and
    snapshot overlap intentionally re-emit acquired records; the normalizer
    owns evidence identity and deduplication.
    """

    if replay_mode not in REPLAY_MODES:
        raise ValueError(f"unsupported replay_mode {replay_mode!r}")
    if not source_surface_id:
        raise ValueError("source_surface_id is required")
    bounds = (
        max_archive_bytes,
        max_conversations_json_bytes,
        max_compression_ratio,
        max_conversations,
        max_messages_per_conversation,
        max_messages,
        max_normalized_chars,
    )
    if any(value < 1 for value in bounds):
        raise ValueError("all import bounds must be positive")

    acquired_item_ids: list[str] = []
    failure_ids: list[str] = []
    human_count = 0
    assistant_count = 0
    conversation_count = 0
    stopped_at_bound = False
    cursor = _find_cursor(graph, source_surface_id)

    try:
        source, input_kind, conversations = _read_conversations(
            export_path,
            max_archive_bytes=max_archive_bytes,
            max_json_bytes=max_conversations_json_bytes,
            max_compression_ratio=max_compression_ratio,
        )
    except (OSError, ExportFormatError) as exc:
        failure_ids.append(
            _record_failure(
                graph,
                source_surface_id=source_surface_id,
                source_ref=str(Path(export_path).expanduser()),
                error_code="invalid_export",
                message=str(exc),
                recoverable=isinstance(exc, OSError),
            )
        )
        return {
            "ok": False,
            "imported": 0,
            "failed": 1,
            "conversations": 0,
            "human_messages": 0,
            "assistant_messages": 0,
            "cursor_id": cursor.id if cursor is not None else None,
            "acquired_item_ids": [],
            "failure_ids": failure_ids,
            "stopped_at_bound": False,
        }

    source_base = f"{source}!/{CONVERSATIONS_MEMBER}" if input_kind == "zip" else str(source)
    if len(conversations) > max_conversations:
        stopped_at_bound = True
        failure_ids.append(
            _record_failure(
                graph,
                source_surface_id=source_surface_id,
                source_ref=source_base,
                error_code="conversation_bound_reached",
                message=f"export has {len(conversations)} conversations; limit is {max_conversations}",
                recoverable=True,
                metadata={"available": len(conversations), "limit": max_conversations},
            )
        )
    for index, conversation in enumerate(conversations[:max_conversations]):
        raw_uuid = conversation.get("uuid") if isinstance(conversation, dict) else None
        conversation_ref = f"{source_base}#conversation={quote(str(raw_uuid or index))}"
        try:
            records = _conversation_records(
                conversation,
                max_messages_per_conversation=max_messages_per_conversation,
                max_normalized_chars=max_normalized_chars,
            )
        except ExportFormatError as exc:
            failure_ids.append(
                _record_failure(
                    graph,
                    source_surface_id=source_surface_id,
                    source_ref=conversation_ref,
                    error_code="invalid_conversation",
                    message=str(exc),
                    metadata={"conversation_index": index, "conversation_uuid": raw_uuid},
                )
            )
            continue
        if len(acquired_item_ids) + len(records) > max_messages:
            stopped_at_bound = True
            failure_ids.append(
                _record_failure(
                    graph,
                    source_surface_id=source_surface_id,
                    source_ref=conversation_ref,
                    error_code="message_bound_reached",
                    message=(
                        f"conversation would exceed message limit {max_messages}; "
                        "conversation was not partially emitted"
                    ),
                    recoverable=True,
                    metadata={"conversation_index": index, "message_count": len(records)},
                )
            )
            break

        conversation_count += 1
        for record in records:
            source_ref = (
                f"{source_base}"
                f"#conversation={quote(record['conversation_uuid'])}"
                f"&message={quote(record['message_uuid'])}"
            )
            try:
                replay_ref, replay_hash = _replay_reference(
                    record["replay_payload"],
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
                        metadata={"provider_item_id": record["provider_item_id"]},
                    )
                )
                continue

            acquired = graph.add_object(
                "acquired_item",
                {
                    "source_surface_id": source_surface_id,
                    "provider_item_id": record["provider_item_id"],
                    "dedup_key": record["dedup_key"],
                    "source_ref": source_ref,
                    "source_hash": record["source_hash"],
                    "provider_time": record["provider_time"],
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
                    "normalized_content": record["normalized_content"],
                    "normalized_metadata": record["normalized_metadata"],
                    "source_category": "ai_activity",
                    "connection_path": "export",
                    "is_fixture": bool(is_fixture),
                },
            )
            acquired_item_ids.append(acquired.id)
            if record["sender"] == "human":
                human_count += 1
            elif record["sender"] == "assistant":
                assistant_count += 1
            cursor = _advance_cursor(
                graph, cursor, source_surface_id, record["provider_item_id"]
            )

    return {
        "ok": not failure_ids,
        "imported": len(acquired_item_ids),
        "failed": len(failure_ids),
        "conversations": conversation_count,
        "human_messages": human_count,
        "assistant_messages": assistant_count,
        "cursor_id": cursor.id if cursor is not None else None,
        "acquired_item_ids": acquired_item_ids,
        "failure_ids": failure_ids,
        "stopped_at_bound": stopped_at_bound,
    }


@tool(
    name="import_claude_export",
    description=(
        "Acquire a bounded official Claude data export (ZIP archive or bare "
        "conversations.json). Emits one Activity Normalizer acquired-item/"
        "content handoff per chat message; identity and dedup stay "
        "normalizer-owned."
    ),
)
def import_claude_export(
    graph,
    export_path: str = "",
    source_surface_id: str = "",
    artifact_store_dir: str = ".activegraph/replay-artifacts",
    replay_mode: str = "artifact",
    is_fixture: bool = False,
    max_archive_bytes: int = 1_000_000_000,
    max_conversations_json_bytes: int = 256_000_000,
    max_compression_ratio: int = 200,
    max_conversations: int = 10_000,
    max_messages_per_conversation: int = 10_000,
    max_messages: int = 250_000,
    max_normalized_chars: int = 32_000,
) -> dict[str, Any]:
    return import_claude_export_fn(
        graph,
        export_path,
        source_surface_id,
        artifact_store_dir=artifact_store_dir,
        replay_mode=replay_mode,
        is_fixture=is_fixture,
        max_archive_bytes=max_archive_bytes,
        max_conversations_json_bytes=max_conversations_json_bytes,
        max_compression_ratio=max_compression_ratio,
        max_conversations=max_conversations,
        max_messages_per_conversation=max_messages_per_conversation,
        max_messages=max_messages,
        max_normalized_chars=max_normalized_chars,
    )


TOOLS = [import_claude_export]

__all__ = [
    "IMPORTER_ID",
    "IMPORTER_VERSION",
    "ExportFormatError",
    "import_claude_export_fn",
    "import_claude_export",
    "TOOLS",
]
