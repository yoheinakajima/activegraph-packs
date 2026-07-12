"""Ingest one assistant self-summary as strict normalizer handoffs.

Identity rule (ADR 0025): the dedup key is the SHA-256 of the canonical
summary text, so the same summary pasted (``manual``) or pushed
(``mcp``) lands on one evidence identity — the normalizer sees the same
``(source_surface_id, dedup_key)`` and the same content hash, and
re-submission through any transport is a no-op at the evidence layer.

The text is untrusted external content: ``scan_for_injection`` labels
land in the normalized metadata (auditable, queryable) and the pipeline
structure keeps a hostile summary inert — evidence → annotations →
candidates, nothing that acts, approves, or escalates.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from activegraph.packs import tool

from packs.tool_gateway.untrusted import scan_for_injection

IMPORTER_ID = "assistant_self_summary"
IMPORTER_VERSION = "0.1.0"
DEFAULT_SURFACE_ID = "assistant_self_summary"
TRANSPORTS = ("manual", "mcp")


def canonical_summary_text(text: str) -> str:
    """The transport-independent canonical form of a pasted summary.

    Conservative on purpose: newline normalization and outer whitespace
    only, so cosmetic copy/paste differences collapse while distinct
    summaries stay distinct.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _record_failure(
    graph,
    *,
    source_surface_id: str,
    error_code: str,
    message: str,
    metadata: Optional[dict[str, Any]] = None,
) -> str:
    failure = graph.add_object(
        "ingestion_failure",
        {
            "source_surface_id": source_surface_id,
            "acquired_item_id": None,
            "source_ref": f"{IMPORTER_ID}:submission",
            "stage": "acquisition",
            "error_code": error_code,
            "message": str(message)[:500],
            "importer_id": IMPORTER_ID,
            "importer_version": IMPORTER_VERSION,
            "extractor_id": None,
            "extractor_version": None,
            "recoverable": False,
            "metadata": dict(metadata or {}),
        },
    )
    return failure.id


def import_assistant_self_summary_fn(
    graph,
    text: str,
    *,
    transport: str = "manual",
    source_surface_id: str = DEFAULT_SURFACE_ID,
    assistant_name: Optional[str] = None,
    provider_time: Optional[str] = None,
    is_fixture: bool = False,
    max_summary_chars: int = 64_000,
    max_normalized_chars: int = 32_000,
) -> dict[str, Any]:
    """Acquire one self-summary submission.

    Re-submission of the same canonical text (any transport) re-emits an
    acquisition record by design; the normalizer owns evidence identity
    and dedups it to zero new revisions.
    """
    if transport not in TRANSPORTS:
        raise ValueError(f"transport must be one of {TRANSPORTS}, got {transport!r}")

    canonical = canonical_summary_text(text or "")
    if not canonical:
        return {
            "ok": False,
            "imported": 0,
            "failure_ids": [
                _record_failure(
                    graph,
                    source_surface_id=source_surface_id,
                    error_code="empty_summary",
                    message="self-summary is empty after canonicalization",
                    metadata={"transport": transport},
                )
            ],
        }
    if len(canonical) > max_summary_chars:
        return {
            "ok": False,
            "imported": 0,
            "failure_ids": [
                _record_failure(
                    graph,
                    source_surface_id=source_surface_id,
                    error_code="summary_too_large",
                    message=(
                        f"self-summary is {len(canonical)} chars; "
                        f"limit is {max_summary_chars}"
                    ),
                    metadata={"transport": transport, "chars": len(canonical)},
                )
            ],
        }

    content_hash = _sha256(canonical.encode("utf-8"))
    dedup_key = f"self_summary:{content_hash}"
    injection_flags = scan_for_injection(canonical)

    acquired = graph.add_object(
        "acquired_item",
        {
            "source_surface_id": source_surface_id,
            "provider_item_id": dedup_key,
            "dedup_key": dedup_key,
            "source_ref": f"{IMPORTER_ID}:{transport}:{content_hash[:12]}",
            "source_hash": content_hash,
            "provider_time": provider_time,
            # Pasted text is ephemeral unique content — inline replay is
            # the sanctioned mode (ADR 0013/0015); the payload IS the ref.
            "replay_mode": "inline",
            "replay_payload_ref": canonical,
            "replay_payload_hash": content_hash,
            "media_type": "text/markdown",
            "importer_id": IMPORTER_ID,
            "importer_version": IMPORTER_VERSION,
        },
    )
    graph.add_object(
        "acquired_content",
        {
            "acquired_item_id": acquired.id,
            "normalized_content": canonical[:max_normalized_chars],
            "normalized_metadata": {
                "subject_scope": "owner_profile",
                "seed_kind": "self_summary",
                "transport": transport,
                "assistant_name": assistant_name,
                "role": "assistant",
                "injection_flags": injection_flags,
                "char_count": len(canonical),
                "truncated": len(canonical) > max_normalized_chars,
            },
            "source_category": "ai_activity",
            "connection_path": transport,
            "is_fixture": bool(is_fixture),
        },
    )
    return {
        "ok": True,
        "imported": 1,
        "acquired_item_id": acquired.id,
        "dedup_key": dedup_key,
        "content_hash": content_hash,
        "transport": transport,
        "injection_flags": injection_flags,
        "failure_ids": [],
    }


@tool(
    name="import_assistant_self_summary",
    description=(
        "Ingest one assistant self-summary (pasted or MCP-pushed). The "
        "same text through either transport lands on one evidence "
        "identity; the text is injection-scanned untrusted content."
    ),
)
def import_assistant_self_summary(
    graph,
    text: str = "",
    transport: str = "manual",
    source_surface_id: str = DEFAULT_SURFACE_ID,
    assistant_name: str = "",
    provider_time: str = "",
    is_fixture: bool = False,
    max_summary_chars: int = 64_000,
    max_normalized_chars: int = 32_000,
) -> dict[str, Any]:
    return import_assistant_self_summary_fn(
        graph,
        text,
        transport=transport,
        source_surface_id=source_surface_id,
        assistant_name=assistant_name or None,
        provider_time=provider_time or None,
        is_fixture=is_fixture,
        max_summary_chars=max_summary_chars,
        max_normalized_chars=max_normalized_chars,
    )


TOOLS = [import_assistant_self_summary]

__all__ = [
    "IMPORTER_ID",
    "IMPORTER_VERSION",
    "DEFAULT_SURFACE_ID",
    "TRANSPORTS",
    "canonical_summary_text",
    "import_assistant_self_summary_fn",
    "import_assistant_self_summary",
    "TOOLS",
]
