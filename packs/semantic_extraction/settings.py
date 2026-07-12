"""Bounds and extractor selection for the shared annotation layer.

These are load-time bounds. Facet policy per source category is NOT
here — that is the ``extraction_profile`` config artifact in the graph
(D042); settings only seed its first version.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .facets import DEFAULT_EAGER_FLOOR


class SemanticExtractionSettings(BaseModel):
    enabled: bool = Field(default=True)
    extractor_id: str = Field(default="semantic.deterministic")
    extractor_version: str = Field(default="0.1.0")
    max_content_chars: int = Field(default=32_000, ge=1_000, le=1_000_000)
    max_annotations_per_facet: int = Field(default=50, ge=1, le=1_000)
    min_assertion_chars: int = Field(default=15, ge=1, le=500)
    topic_tag_count: int = Field(default=5, ge=1, le=50)
    seed_default_profile: bool = Field(
        default=True,
        description="Seed extraction_profile v1 on pack load if none exists.",
    )
    default_profile_facets: tuple[str, ...] = Field(
        default=DEFAULT_EAGER_FLOOR,
        description="Facet floor written into the seeded profile (D041).",
    )
    default_facets_by_source_category: dict[str, tuple[str, ...]] = Field(
        default_factory=lambda: {
            "communication": (
                "entity_mention",
                "question",
                "temporal_expression",
            ),
        },
        description=(
            "Seeded per-category facet policy. High-volume, multi-subject "
            "sources use a bounded structural floor."
        ),
    )
    mint_profile_candidates: bool = Field(default=True)
    mint_memory_candidates: bool = Field(default=True)

    # -- LLM-backed extractor (semantic.llm, D025 stage two) -------------
    llm_upgrade_enabled: bool = Field(
        default=True,
        description=(
            "With a provider configured, seed the default profile with "
            "relation_mention/event_mention routed to semantic.llm. "
            "No provider → this flag changes nothing."
        ),
    )
    llm_model: str = Field(
        default="",
        description=(
            "Model for semantic.llm; empty resolves the configured "
            "provider's default_model. Part of the extractor's cache "
            "identity — pin it explicitly when replaying records."
        ),
    )
    llm_record_dir: str = Field(
        default="",
        description=(
            "Directory of recorded LLM responses (the runtime's "
            "prompt-hash-keyed fixture format). Replays are served from "
            "here first; only unseen prompts reach the live provider. "
            "Empty disables the record layer (the extraction_run cache "
            "still prevents same-identity re-runs)."
        ),
    )
    llm_max_output_tokens: int = Field(default=4_096, ge=256, le=32_000)
    llm_timeout_seconds: float = Field(default=60.0, gt=0.0, le=600.0)


__all__ = ["SemanticExtractionSettings"]
