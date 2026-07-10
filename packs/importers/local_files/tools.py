"""Bounded, deterministic local text/Markdown/JSON acquisition.

This module deliberately owns only the file-format edge. It never creates
evidence, computes evidence identity, deduplicates, or promotes candidates.
For every valid source file it records an ``acquired_item`` followed by its
provider-neutral ``acquired_content`` handoff. Activity Normalizer reacts to
those graph objects later.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Optional

from activegraph.packs import tool


IMPORTER_ID = "local_files"
IMPORTER_VERSION = "0.1.0"
DEFAULT_EXTENSIONS = (".txt", ".md", ".markdown", ".json")
REPLAY_MODES = frozenset({"inline", "artifact", "reference_only"})
REFERENCE_ONLY_SENTINEL = "reference_only:no-payload"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _record_failure(
    graph,
    *,
    source_surface_id: Optional[str],
    source_ref: Optional[str],
    error_code: str,
    message: str,
    provider_item_id: Optional[str] = None,
    recoverable: bool = False,
    metadata: Optional[dict[str, Any]] = None,
) -> str:
    """Append one bounded acquisition failure and return its graph id."""
    details = dict(metadata or {})
    if provider_item_id is not None:
        details["provider_item_id"] = provider_item_id
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
            "metadata": details,
        },
    )
    return failure.id


def _artifact_ref(payload: bytes, artifact_store_dir: str) -> tuple[str, str]:
    """Persist exact replay bytes once and return (URI, digest).

    Layout is the shared v0 contract::

        <artifact_store>/sha256/<first-two-hex>/<full-hex>

    Existing content is verified before reuse. Writes use an atomic rename so
    interruption leaves either the prior complete artifact or no artifact.
    """
    digest = _sha256(payload)
    root = Path(artifact_store_dir).expanduser().resolve()
    target = root / "sha256" / digest[:2] / digest
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        if (
            target.is_symlink()
            or not target.is_file()
            or _sha256(target.read_bytes()) != digest
        ):
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
    text: str,
    *,
    replay_mode: str,
    artifact_store_dir: str,
) -> tuple[str, str]:
    payload = text.encode("utf-8")
    digest = _sha256(payload)
    if replay_mode == "inline":
        return text, digest
    if replay_mode == "reference_only":
        return REFERENCE_ONLY_SENTINEL, digest
    ref, stored_digest = _artifact_ref(payload, artifact_store_dir)
    return ref, stored_digest


def _media_type(suffix: str) -> str:
    if suffix == ".json":
        return "application/json"
    if suffix in (".md", ".markdown"):
        return "text/markdown"
    return "text/plain"


def _normalize_content(
    text: str,
    *,
    suffix: str,
    max_chars: int,
) -> tuple[str, dict[str, Any]]:
    """Return bounded reasoning content, validating JSON before any emit."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    json_metadata: dict[str, Any] = {}
    if suffix == ".json":
        parsed = json.loads(normalized)
        normalized = json.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        json_metadata["json_root_type"] = type(parsed).__name__
        if isinstance(parsed, dict):
            json_metadata["json_top_level_keys"] = sorted(str(k) for k in parsed)[:100]

    original_chars = len(normalized)
    truncated = original_chars > max_chars
    if truncated:
        normalized = normalized[:max_chars]
    return normalized, {
        "normalized_char_count": original_chars,
        "truncated": truncated,
        **json_metadata,
    }


def _walk_files(root: Path, extensions: set[str]) -> list[Path]:
    """Collect regular non-symlink files without traversing symlink dirs."""
    found: list[Path] = []
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        dirs[:] = sorted(
            d for d in dirs if not (current_path / d).is_symlink()
        )
        for name in sorted(files):
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix.lower() in extensions:
                found.append(path)
    found.sort(key=lambda p: p.relative_to(root).as_posix())
    return found


def _find_cursor(graph, source_surface_id: str):
    for cursor in graph.objects(type="backfill_cursor"):
        if (cursor.data or {}).get("source_surface_id") == source_surface_id:
            return cursor
    return None


def _advance_cursor(graph, cursor, source_surface_id: str, stable_ref: str):
    """Commit stable progress after each fully recorded acquired item."""
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


def import_local_files_fn(
    graph,
    root_path: str,
    source_surface_id: str,
    *,
    artifact_store_dir: str = ".activegraph/replay-artifacts",
    replay_mode: str = "artifact",
    is_fixture: bool = False,
    max_files: int = 1000,
    max_file_bytes: int = 2_000_000,
    max_normalized_chars: int = 8192,
    extensions: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """Acquire one bounded, deterministic snapshot of a local directory.

    Files are committed in sorted relative-path order. A malformed or
    oversized file records an ``ingestion_failure`` and creates no acquired
    objects for that file; valid siblings remain committed. Re-running or
    taking overlapping snapshots intentionally emits the same acquired
    identities again—the Activity Normalizer, not this importer, owns dedup.
    """
    if replay_mode not in REPLAY_MODES:
        raise ValueError(f"unsupported replay_mode {replay_mode!r}")
    if not source_surface_id:
        raise ValueError("source_surface_id is required")
    if max_files < 1 or max_file_bytes < 1 or max_normalized_chars < 1:
        raise ValueError("all import bounds must be positive")

    root = Path(root_path).expanduser()
    failure_ids: list[str] = []
    acquired_item_ids: list[str] = []
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
        return {
            "ok": False,
            "imported": 0,
            "failed": 1,
            "cursor_id": None,
            "acquired_item_ids": [],
            "failure_ids": failure_ids,
            "stopped_at_bound": False,
        }

    root = root.resolve()
    suffixes = {
        (e if str(e).startswith(".") else f".{e}").lower()
        for e in (extensions or DEFAULT_EXTENSIONS)
    }
    candidates = _walk_files(root, suffixes)
    stopped_at_bound = len(candidates) > max_files
    candidates = candidates[:max_files]
    cursor = _find_cursor(graph, source_surface_id)

    for path in candidates:
        relative_ref = path.relative_to(root).as_posix()
        source_ref = str(path.resolve())
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

            try:
                normalized, norm_meta = _normalize_content(
                    text,
                    suffix=path.suffix.lower(),
                    max_chars=max_normalized_chars,
                )
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"malformed JSON at line {exc.lineno}, column {exc.colno}"
                ) from exc

            replay_ref, replay_hash = _replay_reference(
                text,
                replay_mode=replay_mode,
                artifact_store_dir=artifact_store_dir,
            )
            source_hash = _sha256(payload)
        except (OSError, ValueError) as exc:
            code = "invalid_json" if path.suffix.lower() == ".json" and "JSON" in str(exc) else "invalid_file"
            failure_ids.append(
                _record_failure(
                    graph,
                    source_surface_id=source_surface_id,
                    source_ref=source_ref,
                    provider_item_id=relative_ref,
                    error_code=code,
                    message=str(exc),
                    recoverable=isinstance(exc, OSError),
                    metadata={"relative_path": relative_ref},
                )
            )
            continue

        acquired = graph.add_object(
            "acquired_item",
            {
                "source_surface_id": source_surface_id,
                "provider_item_id": relative_ref,
                "dedup_key": relative_ref,
                "source_ref": source_ref,
                "source_hash": source_hash,
                "provider_time": None,
                "replay_mode": replay_mode,
                "replay_payload_ref": replay_ref,
                "replay_payload_hash": replay_hash,
                "media_type": _media_type(path.suffix.lower()),
                "importer_id": IMPORTER_ID,
                "importer_version": IMPORTER_VERSION,
            },
        )
        graph.add_object(
            "acquired_content",
            {
                "acquired_item_id": acquired.id,
                "normalized_content": normalized,
                "normalized_metadata": {
                    "relative_path": relative_ref,
                    "extension": path.suffix.lower(),
                    "byte_size": len(payload),
                    "content_sha256": source_hash,
                    **norm_meta,
                },
                "source_category": "local_knowledge",
                "connection_path": "local",
                "is_fixture": bool(is_fixture),
            },
        )
        acquired_item_ids.append(acquired.id)
        cursor = _advance_cursor(graph, cursor, source_surface_id, relative_ref)

    return {
        "ok": not failure_ids,
        "imported": len(acquired_item_ids),
        "failed": len(failure_ids),
        "cursor_id": cursor.id if cursor is not None else None,
        "acquired_item_ids": acquired_item_ids,
        "failure_ids": failure_ids,
        "stopped_at_bound": stopped_at_bound,
    }


@tool(
    name="import_local_files",
    description=(
        "Acquire a bounded snapshot of UTF-8 text, Markdown, and JSON files. "
        "Emits Activity Normalizer acquired-item/content records; it does not "
        "deduplicate, extract candidates, or promote state."
    ),
)
def import_local_files(
    graph,
    root_path: str,
    source_surface_id: str = "",
    artifact_store_dir: str = ".activegraph/replay-artifacts",
    replay_mode: str = "artifact",
    is_fixture: bool = False,
    max_files: int = 1000,
    max_file_bytes: int = 2_000_000,
    max_normalized_chars: int = 8192,
) -> dict[str, Any]:
    """Registered capability wrapper for :func:`import_local_files_fn`."""
    return import_local_files_fn(
        graph,
        root_path,
        source_surface_id,
        artifact_store_dir=artifact_store_dir,
        replay_mode=replay_mode,
        is_fixture=is_fixture,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_normalized_chars=max_normalized_chars,
    )


TOOLS = [import_local_files]
