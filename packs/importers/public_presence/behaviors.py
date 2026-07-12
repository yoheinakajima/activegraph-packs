"""Land executed presence fetches as evidence — through the injection posture.

Fires on the gateway's ``capability_result``; only results whose call
carries our ``public_presence`` metadata are acquired. Fetched content
is untrusted external content (ADR 0023): it is injection-scanned and
the labels travel in the normalized metadata; downstream it can become
annotations and candidates, never actions.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from activegraph.packs import behavior

from packs.tool_gateway.untrusted import scan_for_injection

from .settings import PublicPresenceSettings
from .tools import IMPORTER_ID, IMPORTER_VERSION


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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


@behavior(
    name="acquire_presence_result",
    on=["object.created"],
    where={"object.type": "capability_result"},
    view={
        "include_types": [
            "capability_call",
            "capability_result",
            "acquired_item",
            "acquired_content",
            "ingestion_failure",
        ]
    },
    creates=["acquired_item", "acquired_content", "ingestion_failure"],
)
def acquire_presence_result(event, graph, ctx, *, settings: PublicPresenceSettings):
    """One executed presence fetch → one strict normalizer handoff."""
    wrapper = event.payload.get("object", {})
    result_data = wrapper.get("data", {})
    call_id = result_data.get("call_id")
    call = graph.get_object(call_id) if call_id else None
    if call is None:
        return
    presence_meta = (call.data.get("metadata") or {}).get("public_presence")
    if not presence_meta:
        return

    surface_id = presence_meta.get("source_surface_id") or "public_presence"
    url = presence_meta.get("url") or ""

    def _fail(error_code: str, message: str) -> None:
        graph.add_object(
            "ingestion_failure",
            {
                "source_surface_id": surface_id,
                "acquired_item_id": None,
                "source_ref": url or f"{IMPORTER_ID}:unknown",
                "stage": "acquisition",
                "error_code": error_code,
                "message": message[:500],
                "importer_id": IMPORTER_ID,
                "importer_version": IMPORTER_VERSION,
                "extractor_id": None,
                "extractor_version": None,
                "recoverable": True,
                "metadata": {"call_id": call_id},
            },
        )

    if not result_data.get("success"):
        _fail("fetch_failed", str(result_data.get("error") or "capability failed"))
        return
    try:
        output = json.loads(result_data.get("output_data") or "{}")
    except json.JSONDecodeError as exc:
        _fail("malformed_fetch_output", f"JSONDecodeError: {exc}")
        return
    text = str(output.get("text") or "")
    if not text.strip():
        _fail("empty_page", f"no text extracted from {url}")
        return

    text = text[: settings.max_page_chars]
    injection_flags = scan_for_injection(text)
    replay_unit = _canonical_json(
        {
            "url": url,
            "final_url": output.get("final_url"),
            "status": output.get("status"),
            "title": output.get("title"),
            "text": text,
        }
    )
    replay_ref, replay_hash = _artifact_ref(
        replay_unit.encode("utf-8"), settings.artifact_store_dir
    )

    item = graph.add_object(
        "acquired_item",
        {
            "source_surface_id": surface_id,
            "provider_item_id": url,
            "dedup_key": f"presence:{url}",
            "source_ref": url,
            "source_hash": _sha256(text.encode("utf-8")),
            "provider_time": result_data.get("executed_at"),
            "replay_mode": "artifact",
            "replay_payload_ref": replay_ref,
            "replay_payload_hash": replay_hash,
            "media_type": "text/plain",
            "importer_id": IMPORTER_ID,
            "importer_version": IMPORTER_VERSION,
        },
    )
    graph.add_object(
        "acquired_content",
        {
            "acquired_item_id": item.id,
            "normalized_content": text,
            "normalized_metadata": {
                "subject_scope": "owner_profile",
                # Public self-description is useful evidence, never authority.
                # Extraction may propose findings, but memory/profile admission
                # requires corroboration or an explicit owner decision.
                "source_trust": "unverified_public",
                "memory_admission": "review_required",
                "url": url,
                "final_url": output.get("final_url"),
                "status": output.get("status"),
                "title": output.get("title"),
                "handle_kind": presence_meta.get("handle_kind"),
                "run_id": presence_meta.get("run_id"),
                "injection_flags": injection_flags,
                "truncated": bool(output.get("truncated"))
                or len(str(output.get("text") or "")) > settings.max_page_chars,
            },
            # Public self-descriptions are knowledge about the owner; the
            # closed category set has no better home and the slice must
            # not invent one (SCORING_CONTRACT: categories are closed).
            "source_category": "local_knowledge",
            "connection_path": "pack",
            "is_fixture": bool(presence_meta.get("is_fixture")),
        },
    )


BEHAVIORS = [acquire_presence_result]

__all__ = ["BEHAVIORS"]
