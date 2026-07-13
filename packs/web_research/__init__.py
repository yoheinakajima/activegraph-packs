"""Consented web research: never ambient, always plan-bound (ADR 0040)."""

from activegraph.packs import Pack

from .object_types import OBJECT_TYPES, RELATION_TYPES
from .plan import register_web_research

register_web_research()

# requires=["tool_gateway", "connector_control", "subject_profile", "activity_normalizer"]
pack = Pack(
    name="web_research",
    version="0.2.0",
    description=(
        "Owner-consented web research: queries derive only from confirmed "
        "subject facts, run as strikeable surfaces of an approved ingestion "
        "plan through the configured model's search, and every discovered "
        "page ingests through the governed public-presence gateway."
    ),
    object_types=tuple(OBJECT_TYPES),
    relation_types=tuple(RELATION_TYPES),
    behaviors=(),
    tools=(),
    policies=(),
    prompts=(),
)

__all__ = ["pack"]
