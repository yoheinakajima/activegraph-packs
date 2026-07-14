"""Consented web research: never ambient, always plan-bound (ADR 0040/0045)."""

from activegraph.packs import Pack

from .object_types import OBJECT_TYPES, RELATION_TYPES
from .plan import register_web_research
from .settings import WebResearchSettings

register_web_research()

# requires=["tool_gateway", "connector_control", "subject_profile", "activity_normalizer"]
pack = Pack(
    name="web_research",
    version="0.4.0",
    description=(
        "Owner-consented adaptive research campaigns: seed queries derive "
        "only from confirmed subject facts and run as strikeable surfaces of "
        "an approved ingestion plan; bounded rounds record a follow-up "
        "frontier with lineage through a provider-neutral search adapter, "
        "scope expansion pauses for approval, deterministic budgets stop the "
        "campaign with a recorded reason, and every discovered page ingests "
        "through the governed public-presence gateway."
    ),
    object_types=tuple(OBJECT_TYPES),
    relation_types=tuple(RELATION_TYPES),
    behaviors=(),
    tools=(),
    policies=(),
    prompts=(),
    settings_schema=WebResearchSettings,
)

__all__ = ["pack", "WebResearchSettings"]
