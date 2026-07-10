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
    mint_profile_candidates: bool = Field(default=True)
    mint_memory_candidates: bool = Field(default=True)


__all__ = ["SemanticExtractionSettings"]
