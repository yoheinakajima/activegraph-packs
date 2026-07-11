"""Settings for Entity Pack."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EntitySettings(BaseModel):
    """Configuration for Entity Pack v0.1.

    Controls entity extraction heuristics, resolution thresholds,
    and merge candidate detection sensitivity.
    """

    extraction_min_confidence: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum confidence for entity_extractor to create an EntityMention. "
            "Mentions below this threshold are still created but not resolved."
        ),
    )

    resolution_similarity_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum similarity score for entity_resolver to link an EntityMention "
            "to an existing Entity. Below this, a new Entity is created."
        ),
    )

    merge_candidate_threshold: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum similarity score for merge_candidate_detector to create a "
            "MergeCandidate. Higher = more conservative (fewer false positives)."
        ),
    )

    auto_accept_exact_identifier_match: bool = Field(
        default=True,
        description=(
            "If True, entity_resolver automatically links a mention to an existing "
            "Entity when there is an exact identifier match (e.g., same email address). "
            "This bypasses the similarity threshold for identifier-confirmed matches."
        ),
    )

    extract_persons: bool = Field(
        default=True,
        description="If True, extract person entity mentions from sources.",
    )

    extract_organizations: bool = Field(
        default=True,
        description="If True, extract organization entity mentions from sources.",
    )

    extract_urls_as_entities: bool = Field(
        default=False,
        description=(
            "If True, extract URL-bearing entities (products, repos) from sources. "
            "Disabled by default to reduce noise."
        ),
    )

    max_mentions_per_source: int = Field(
        default=20,
        ge=1,
        description="Maximum number of EntityMentions to extract from a single source.",
    )

    # -- shared-extraction migration (ADR 0026 step 4) -------------------
    extract_from_raw_sources: bool = Field(
        default=False,
        description=(
            "Re-enable the pack's own source.created extraction path. OFF "
            "by default: entity-mention ownership moved to the shared "
            "extraction contract, and this pack consumes entity_mention "
            "annotations instead. Enable only for pipelines whose sources "
            "do not flow through the shared layer yet (raw email/source "
            "objects)."
        ),
    )
    consume_entity_mention_annotations: bool = Field(
        default=True,
        description=(
            "Mint EntityMention objects from the shared layer's "
            "entity_mention annotations. Canonical resolution (mention → "
            "canonical entity) stays solely in this pack either way."
        ),
    )
