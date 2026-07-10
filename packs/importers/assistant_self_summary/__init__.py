"""Assistant self-summary importer (ADR 0025).

One surface for the assistant-as-source seed: an owner pastes a
self-summary (``manual``) or a connected assistant pushes the same text
over MCP (``mcp``). The transport is connection-path metadata — the same
summary through either transport produces the same evidence identity,
because the dedup key is the canonical content hash.

A self-summary is a lossy seed: it complements exports and local
sessions, never replaces them. Its text is untrusted external content
(ADR 0023 posture): it is injection-scanned on the way in and can become
candidates at most — never actions, approvals, or escalations.
"""

from __future__ import annotations

from activegraph.packs import Pack

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import AssistantSelfSummarySettings
from .tools import TOOLS


# requires=["activity_normalizer"]
# integrates_with=["tool_gateway"]
pack = Pack(
    name="assistant_self_summary",
    version="0.1.0",
    description=(
        "Paste-back or MCP-pushed assistant self-summaries as evidence: "
        "one surface, transport as metadata, identity by canonical "
        "content hash, injection-scanned on ingestion."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=(),
    settings_schema=AssistantSelfSummarySettings,
)

__all__ = ["pack", "AssistantSelfSummarySettings"]
