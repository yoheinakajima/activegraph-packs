"""The standard extraction facets and their typed annotation bodies.

ADR 0026: extraction produces typed, source-anchored annotations sharing
one provenance envelope. The facet set below is the standard closed set;
namespaced extension facets (``"<pack>.<facet>"``) are allowed but none
ship in this slice. The deterministic v1 extractor implements the subset
the bootstrap needs; the remaining facets are contract, not code, so an
upgraded extractor (LLM-backed, same contract, different extractor id)
can fill them without a schema change.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# The standard facet names (ADR 0026 / INGESTION_DOCTRINE). Order is part
# of the contract only in that it is stable for display; nothing keys on it.
STANDARD_FACETS: tuple[str, ...] = (
    "entity_mention",
    "assertion",
    "question",
    "idea",
    "event_mention",
    "relation_mention",
    "preference_expression",
    "temporal_expression",
    "quantity_mention",
    "topic_tag",
)

# The cheap eager floor (D041): entities, assertions, preferences,
# questions, explicit dates. This is the DEFAULT for the seeded
# extraction_profile config artifact — policy, not architecture (D042).
DEFAULT_EAGER_FLOOR: tuple[str, ...] = (
    "assertion",
    "entity_mention",
    "preference_expression",
    "question",
    "temporal_expression",
)

# Facets the deterministic v1 extractor actually produces. Requesting a
# standard facet outside this set is legal; coverage records it as
# skipped ("not_implemented") instead of silently claiming completeness.
V1_IMPLEMENTED_FACETS: tuple[str, ...] = (
    "assertion",
    "entity_mention",
    "preference_expression",
    "question",
    "temporal_expression",
    "topic_tag",
)

# Facets the LLM-backed extractor (semantic.llm) produces: richer results
# where the deterministic floor is weak, plus the two facets the floor
# does not implement at all (relation_mention, event_mention).
LLM_IMPLEMENTED_FACETS: tuple[str, ...] = (
    "assertion",
    "entity_mention",
    "event_mention",
    "preference_expression",
    "relation_mention",
)

# The facets the default profile routes to the LLM extractor when a
# provider is configured (D041: the cheap eager floor stands; the LLM
# serves only what the floor cannot).
LLM_UPGRADE_FACETS: tuple[str, ...] = ("event_mention", "relation_mention")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EntityMentionBody(_StrictModel):
    text: str = Field(min_length=1)
    # The first four kinds are the deterministic floor's; the rest are
    # semantic kinds only an upgraded extractor can judge (people/orgs
    # beyond regex reach). Widening the enum is backward compatible —
    # every existing annotation body still validates.
    kind: Literal[
        "handle", "email", "url", "proper_noun",
        "person", "organization", "place", "product", "other",
    ]
    normalized: str = Field(min_length=1)


class AssertionBody(_StrictModel):
    text: str = Field(min_length=1)


class QuestionBody(_StrictModel):
    text: str = Field(min_length=1)


class PreferenceExpressionBody(_StrictModel):
    text: str = Field(min_length=1)
    cue: str = Field(min_length=1)


class TemporalExpressionBody(_StrictModel):
    text: str = Field(min_length=1)
    normalized: str = Field(min_length=1)
    precision: Literal["day", "month"]


class TopicTagBody(_StrictModel):
    tag: str = Field(min_length=1)
    occurrences: int = Field(ge=1)


class RelationMentionBody(_StrictModel):
    text: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object: str = Field(min_length=1)


class EventMentionBody(_StrictModel):
    text: str = Field(min_length=1)
    name: str = Field(min_length=1)
    participants: list[str] = Field(default_factory=list)
    when: Optional[str] = None


BODY_SCHEMAS: dict[str, type[BaseModel]] = {
    "entity_mention": EntityMentionBody,
    "assertion": AssertionBody,
    "question": QuestionBody,
    "preference_expression": PreferenceExpressionBody,
    "temporal_expression": TemporalExpressionBody,
    "topic_tag": TopicTagBody,
    "relation_mention": RelationMentionBody,
    "event_mention": EventMentionBody,
}


def validate_body(facet: str, body: dict[str, Any]) -> dict[str, Any]:
    """Type-check one annotation body against its facet schema.

    Facets without a shipped schema (the not-yet-implemented standard
    facets and namespaced extension facets) pass through unchanged — the
    envelope is still enforced by the ``semantic_annotation`` object type.
    """
    schema: Optional[type[BaseModel]] = BODY_SCHEMAS.get(facet)
    if schema is None:
        return dict(body)
    return schema(**body).model_dump()


__all__ = [
    "STANDARD_FACETS",
    "DEFAULT_EAGER_FLOOR",
    "V1_IMPLEMENTED_FACETS",
    "LLM_IMPLEMENTED_FACETS",
    "LLM_UPGRADE_FACETS",
    "BODY_SCHEMAS",
    "validate_body",
]
