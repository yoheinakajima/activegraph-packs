"""Immutable replay-artifact storage and verification helpers."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Union


Payload = Union[str, bytes]

_ARTIFACT_REF_RE = re.compile(
    r"^artifact://sha256/(?P<prefix>[0-9a-f]{2})/(?P<digest>[0-9a-f]{64})$"
)
_LEGACY_REF_RE = re.compile(r"^sha256:(?P<digest>[0-9a-f]{64})$")


class ReplayError(RuntimeError):
    """Base class for deterministic replay failures."""


class ReplayUnavailableError(ReplayError):
    """The evidence deliberately or accidentally has no replay payload."""


class ReplayIntegrityError(ReplayError):
    """The retained payload does not match its recorded SHA-256 identity."""


def sha256_hex(payload: Payload) -> str:
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    return hashlib.sha256(data).hexdigest()


def artifact_ref_for_hash(digest: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("digest must be lowercase SHA-256 hex")
    return f"artifact://sha256/{digest[:2]}/{digest}"


def _digest_from_ref(ref: str) -> str:
    match = _ARTIFACT_REF_RE.fullmatch(ref)
    if match:
        digest = match.group("digest")
        if match.group("prefix") != digest[:2]:
            raise ReplayIntegrityError("artifact reference prefix does not match digest")
        return digest
    legacy = _LEGACY_REF_RE.fullmatch(ref)
    if legacy:
        return legacy.group("digest")
    raise ReplayIntegrityError("invalid replay artifact reference")


def artifact_path(artifact_store_dir: str | Path, ref_or_digest: str) -> Path:
    digest = (
        ref_or_digest
        if re.fullmatch(r"[0-9a-f]{64}", ref_or_digest)
        else _digest_from_ref(ref_or_digest)
    )
    return Path(artifact_store_dir) / "sha256" / digest[:2] / digest


def store_replay_artifact(payload: Payload, artifact_store_dir: str | Path) -> tuple[str, str]:
    """Store payload once and return its canonical reference and bare digest."""

    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    digest = sha256_hex(data)
    path = artifact_path(artifact_store_dir, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_bytes()
        if sha256_hex(existing) != digest:
            raise ReplayIntegrityError(f"existing artifact is corrupt: {path}")
    else:
        temporary = path.with_name(f".{digest}.tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)
    return artifact_ref_for_hash(digest), digest


def read_artifact(
    ref: str,
    expected_hash: str,
    artifact_store_dir: str | Path,
    *,
    max_bytes: int,
) -> bytes:
    digest = _digest_from_ref(ref)
    if digest != expected_hash:
        raise ReplayIntegrityError("artifact reference and replay hash disagree")
    path = artifact_path(artifact_store_dir, digest)
    if not path.is_file():
        raise ReplayUnavailableError(f"replay artifact is missing: {ref}")
    if path.stat().st_size > max_bytes:
        raise ReplayUnavailableError("replay artifact exceeds configured size bound")
    payload = path.read_bytes()
    if sha256_hex(payload) != expected_hash:
        raise ReplayIntegrityError("replay artifact hash verification failed")
    return payload


def read_replay_payload(evidence: dict, settings) -> bytes:
    """Load only the retained replay payload; never dereference ``source_ref``."""

    mode = evidence.get("replay_mode")
    expected_hash = evidence.get("replay_payload_hash", "")
    if mode == "reference_only":
        raise ReplayUnavailableError("reference_only evidence is not replay-complete")
    if mode == "inline":
        payload = str(evidence.get("replay_payload_ref", "")).encode(
            evidence.get("encoding") or settings.encoding
        )
        if sha256_hex(payload) != expected_hash:
            raise ReplayIntegrityError("inline replay payload hash verification failed")
        return payload
    if mode == "artifact":
        return read_artifact(
            str(evidence.get("replay_payload_ref", "")),
            expected_hash,
            settings.artifact_store_dir,
            max_bytes=settings.max_replay_payload_bytes,
        )
    raise ReplayUnavailableError(f"unsupported replay mode: {mode!r}")


__all__ = [
    "ReplayError",
    "ReplayUnavailableError",
    "ReplayIntegrityError",
    "sha256_hex",
    "artifact_ref_for_hash",
    "artifact_path",
    "store_replay_artifact",
    "read_artifact",
    "read_replay_payload",
]
