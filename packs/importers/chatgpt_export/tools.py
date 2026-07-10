"""Bounded parser for official ChatGPT export ZIP snapshots.

``conversations.json`` is a tree, not a transcript.  The path ending at the
provider's ``current_node`` is emitted as canonical conversation evidence;
every other message node is also emitted and explicitly marked as an
abandoned edit/regeneration branch (a useful correction signal).
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


IMPORTER_ID = "chatgpt_export"
IMPORTER_VERSION = "0.1.0"
CONVERSATIONS_MEMBER = "conversations.json"
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


def _part_text(part: Any) -> str:
    if isinstance(part, str):
        return part
    if part is None:
        return ""
    if isinstance(part, (int, float, bool)):
        return str(part)
    if not isinstance(part, dict):
        return ""
    for key in ("text", "content", "result"):
        if isinstance(part.get(key), str):
            return part[key]
    nested = part.get("parts")
    if isinstance(nested, list):
        return "\n".join(filter(None, (_part_text(item) for item in nested)))
    kind = part.get("content_type") or part.get("type") or "non_text"
    return f"[{kind} content omitted]"


def _normalized_message(message: dict[str, Any], max_chars: int) -> tuple[str, dict[str, Any]]:
    content = message.get("content")
    content_type = None
    parts: list[Any] = []
    if isinstance(content, dict):
        content_type = content.get("content_type") or content.get("type")
        if isinstance(content.get("parts"), list):
            parts = content["parts"]
        elif isinstance(content.get("text"), str):
            parts = [content["text"]]
        else:
            parts = [content]
    elif isinstance(content, str):
        parts = [content]
    elif content is not None:
        parts = [content]

    text = "\n".join(filter(None, (_part_text(part) for part in parts)))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    original_chars = len(text)
    truncated = original_chars > max_chars
    if truncated:
        text = text[:max_chars]
    return text, {
        "content_type": str(content_type) if content_type is not None else None,
        "part_count": len(parts),
        "normalized_char_count": original_chars,
        "truncated": truncated,
    }


def _author(message: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    author = message.get("author")
    if not isinstance(author, dict):
        return None, None
    role = author.get("role")
    name = author.get("name")
    return (
        str(role) if role is not None else None,
        str(name) if name is not None else None,
    )


def _validate_mapping(mapping: dict[str, Any]) -> None:
    """Validate node identity and parent chains before emitting a conversation."""

    for node_id, node in mapping.items():
        if not isinstance(node_id, str) or not node_id:
            raise ExportFormatError("mapping keys must be non-empty strings")
        if not isinstance(node, dict):
            raise ExportFormatError(f"node {node_id!r} is not an object")
        declared_id = node.get("id")
        if declared_id is not None and str(declared_id) != node_id:
            raise ExportFormatError(f"node id mismatch for mapping key {node_id!r}")
        parent = node.get("parent")
        if parent is not None and not isinstance(parent, str):
            raise ExportFormatError(f"node {node_id!r} has a non-string parent")
        children = node.get("children", [])
        if children is not None and (
            not isinstance(children, list) or any(not isinstance(child, str) for child in children)
        ):
            raise ExportFormatError(f"node {node_id!r} has invalid children")

        visited: set[str] = set()
        current: Optional[str] = node_id
        while current is not None:
            if current in visited:
                raise ExportFormatError(f"cycle detected at node {current!r}")
            visited.add(current)
            current_node = mapping.get(current)
            if current_node is None:
                raise ExportFormatError(f"node {node_id!r} references missing parent {current!r}")
            current = current_node.get("parent")


def _canonical_path(
    mapping: dict[str, Any], current_node: Any
) -> tuple[list[str], str, str]:
    """Return root-to-current path, selected leaf, and selection provenance."""

    selection = "provider_current_node"
    if current_node is None:
        leaves = []
        for node_id, node in mapping.items():
            children = [child for child in (node.get("children") or []) if child in mapping]
            if not children and isinstance(node.get("message"), dict):
                message = node["message"]
                leaves.append((str(message.get("create_time") or ""), node_id))
        if not leaves:
            raise ExportFormatError("conversation has no current_node or message leaf")
        current_node = max(leaves)[1]
        selection = "deterministic_latest_leaf_fallback"
    if not isinstance(current_node, str) or current_node not in mapping:
        raise ExportFormatError("current_node does not identify a mapping node")

    path: list[str] = []
    cursor: Optional[str] = current_node
    while cursor is not None:
        path.append(cursor)
        cursor = mapping[cursor].get("parent")
    path.reverse()
    return path, current_node, selection


def _nearest_canonical_ancestor(
    node_id: str, mapping: dict[str, Any], canonical: set[str]
) -> Optional[str]:
    parent = mapping[node_id].get("parent")
    while parent is not None:
        if parent in canonical:
            return parent
        parent = mapping[parent].get("parent")
    return None


def _conversation_records(
    conversation: Any,
    *,
    max_nodes: int,
    max_normalized_chars: int,
) -> list[dict[str, Any]]:
    if not isinstance(conversation, dict):
        raise ExportFormatError("conversation entry is not an object")
    conversation_id = conversation.get("id") or conversation.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id:
        raise ExportFormatError("conversation has no stable id")
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        raise ExportFormatError(f"conversation {conversation_id!r} has no mapping")
    if len(mapping) > max_nodes:
        raise ExportFormatError(
            f"conversation {conversation_id!r} has {len(mapping)} nodes; limit is {max_nodes}"
        )
    _validate_mapping(mapping)
    canonical_path, canonical_leaf, selection = _canonical_path(
        mapping, conversation.get("current_node")
    )
    canonical_set = set(canonical_path)
    canonical_position = {node_id: index for index, node_id in enumerate(canonical_path)}

    message_ids = [
        node_id for node_id, node in mapping.items() if isinstance(node.get("message"), dict)
    ]
    ordered_ids = [node_id for node_id in canonical_path if node_id in message_ids]
    ordered_ids.extend(sorted(node_id for node_id in message_ids if node_id not in canonical_set))

    title = conversation.get("title")
    records: list[dict[str, Any]] = []
    for node_id in ordered_ids:
        node = mapping[node_id]
        message = node["message"]
        text, content_meta = _normalized_message(message, max_normalized_chars)
        role, author_name = _author(message)
        branch_status = "canonical" if node_id in canonical_set else "abandoned"
        children = list(node.get("children") or [])
        parent_id = node.get("parent")
        sibling_index = None
        if parent_id is not None:
            siblings = list(mapping[parent_id].get("children") or [])
            if node_id in siblings:
                sibling_index = siblings.index(node_id)
        provider_time = _provider_time(
            message.get("create_time")
            or message.get("update_time")
            or conversation.get("update_time")
            or conversation.get("create_time")
        )

        normalized_metadata = {
            "conversation_id": conversation_id,
            "conversation_title": str(title) if title is not None else None,
            "node_id": node_id,
            "message_id": str(message.get("id") or node_id),
            "parent_id": parent_id,
            "children_ids": children,
            "sibling_index": sibling_index,
            "role": role,
            "author_name": author_name,
            "message_status": message.get("status"),
            "recipient": message.get("recipient"),
            "branch_status": branch_status,
            "is_canonical": branch_status == "canonical",
            "correction_signal": branch_status == "abandoned",
            "canonical_path_index": canonical_position.get(node_id),
            "canonical_leaf_id": canonical_leaf,
            "canonical_selection": selection,
            "nearest_canonical_ancestor_id": (
                None
                if branch_status == "canonical"
                else _nearest_canonical_ancestor(node_id, mapping, canonical_set)
            ),
            "provider_time": provider_time,
            **content_meta,
        }
        replay_unit = {
            "conversation_id": conversation_id,
            "conversation_title": str(title) if title is not None else None,
            "node_id": node_id,
            "parent_id": parent_id,
            "children_ids": children,
            "message_id": str(message.get("id") or node_id),
            "role": role,
            "author_name": author_name,
            "provider_time": provider_time,
            "branch_status": branch_status,
            "normalized_content": text,
            "normalized_metadata": normalized_metadata,
        }
        records.append(
            {
                "conversation_id": conversation_id,
                "node_id": node_id,
                "dedup_key": f"{conversation_id}:{node_id}",
                "provider_item_id": f"{conversation_id}:{node_id}",
                "provider_time": provider_time,
                "source_hash": _sha256(_canonical_json(node).encode("utf-8")),
                "normalized_content": text,
                "normalized_metadata": normalized_metadata,
                "replay_payload": _canonical_json(replay_unit),
                "branch_status": branch_status,
            }
        )
    return records


def _read_conversations(
    archive_path: str,
    *,
    max_archive_bytes: int,
    max_json_bytes: int,
    max_compression_ratio: int,
) -> tuple[Path, list[Any]]:
    archive = Path(archive_path).expanduser()
    if archive.is_symlink() or not archive.is_file():
        raise ExportFormatError("archive_path must be an existing, non-symlink ZIP file")
    if archive.stat().st_size > max_archive_bytes:
        raise ExportFormatError(
            f"archive is {archive.stat().st_size} bytes; limit is {max_archive_bytes}"
        )
    archive = archive.resolve()
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
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
    try:
        parsed = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportFormatError(f"malformed conversations.json: {exc}") from exc
    if not isinstance(parsed, list):
        raise ExportFormatError("conversations.json root must be a list")
    return archive, parsed


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


def import_chatgpt_export_fn(
    graph,
    archive_path: str,
    source_surface_id: str,
    *,
    artifact_store_dir: str = ".activegraph/replay-artifacts",
    replay_mode: str = "artifact",
    is_fixture: bool = False,
    max_archive_bytes: int = 1_000_000_000,
    max_conversations_json_bytes: int = 256_000_000,
    max_compression_ratio: int = 200,
    max_conversations: int = 10_000,
    max_nodes_per_conversation: int = 100_000,
    max_messages: int = 250_000,
    max_normalized_chars: int = 32_000,
) -> dict[str, Any]:
    """Acquire one bounded official ChatGPT export snapshot.

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
        max_nodes_per_conversation,
        max_messages,
        max_normalized_chars,
    )
    if any(value < 1 for value in bounds):
        raise ValueError("all import bounds must be positive")

    acquired_item_ids: list[str] = []
    failure_ids: list[str] = []
    canonical_count = 0
    abandoned_count = 0
    conversation_count = 0
    stopped_at_bound = False
    cursor = _find_cursor(graph, source_surface_id)

    try:
        archive, conversations = _read_conversations(
            archive_path,
            max_archive_bytes=max_archive_bytes,
            max_json_bytes=max_conversations_json_bytes,
            max_compression_ratio=max_compression_ratio,
        )
    except (OSError, ExportFormatError) as exc:
        failure_ids.append(
            _record_failure(
                graph,
                source_surface_id=source_surface_id,
                source_ref=str(Path(archive_path).expanduser()),
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
            "canonical_messages": 0,
            "abandoned_messages": 0,
            "cursor_id": cursor.id if cursor is not None else None,
            "acquired_item_ids": [],
            "failure_ids": failure_ids,
            "stopped_at_bound": False,
        }

    if len(conversations) > max_conversations:
        stopped_at_bound = True
        failure_ids.append(
            _record_failure(
                graph,
                source_surface_id=source_surface_id,
                source_ref=f"{archive}!/{CONVERSATIONS_MEMBER}",
                error_code="conversation_bound_reached",
                message=f"export has {len(conversations)} conversations; limit is {max_conversations}",
                recoverable=True,
                metadata={"available": len(conversations), "limit": max_conversations},
            )
        )
    for index, conversation in enumerate(conversations[:max_conversations]):
        raw_id = conversation.get("id") if isinstance(conversation, dict) else None
        conversation_ref = f"{archive}!/{CONVERSATIONS_MEMBER}#conversation={quote(str(raw_id or index))}"
        try:
            records = _conversation_records(
                conversation,
                max_nodes=max_nodes_per_conversation,
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
                    metadata={"conversation_index": index, "conversation_id": raw_id},
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
                f"{archive}!/{CONVERSATIONS_MEMBER}"
                f"#conversation={quote(record['conversation_id'])}&node={quote(record['node_id'])}"
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
            if record["branch_status"] == "canonical":
                canonical_count += 1
            else:
                abandoned_count += 1
            cursor = _advance_cursor(
                graph, cursor, source_surface_id, record["provider_item_id"]
            )

    return {
        "ok": not failure_ids,
        "imported": len(acquired_item_ids),
        "failed": len(failure_ids),
        "conversations": conversation_count,
        "canonical_messages": canonical_count,
        "abandoned_messages": abandoned_count,
        "cursor_id": cursor.id if cursor is not None else None,
        "acquired_item_ids": acquired_item_ids,
        "failure_ids": failure_ids,
        "stopped_at_bound": stopped_at_bound,
    }


@tool(
    name="import_chatgpt_export",
    description=(
        "Acquire a bounded official ChatGPT export ZIP. Preserves the current "
        "conversation path and abandoned edit/regeneration branches as distinct "
        "Activity Normalizer handoffs."
    ),
)
def import_chatgpt_export(
    graph,
    archive_path: str = "",
    source_surface_id: str = "",
    artifact_store_dir: str = ".activegraph/replay-artifacts",
    replay_mode: str = "artifact",
    is_fixture: bool = False,
    max_archive_bytes: int = 1_000_000_000,
    max_conversations_json_bytes: int = 256_000_000,
    max_compression_ratio: int = 200,
    max_conversations: int = 10_000,
    max_nodes_per_conversation: int = 100_000,
    max_messages: int = 250_000,
    max_normalized_chars: int = 32_000,
) -> dict[str, Any]:
    return import_chatgpt_export_fn(
        graph,
        archive_path,
        source_surface_id,
        artifact_store_dir=artifact_store_dir,
        replay_mode=replay_mode,
        is_fixture=is_fixture,
        max_archive_bytes=max_archive_bytes,
        max_conversations_json_bytes=max_conversations_json_bytes,
        max_compression_ratio=max_compression_ratio,
        max_conversations=max_conversations,
        max_nodes_per_conversation=max_nodes_per_conversation,
        max_messages=max_messages,
        max_normalized_chars=max_normalized_chars,
    )


TOOLS = [import_chatgpt_export]

__all__ = [
    "IMPORTER_ID",
    "IMPORTER_VERSION",
    "ExportFormatError",
    "import_chatgpt_export_fn",
    "import_chatgpt_export",
    "TOOLS",
]
