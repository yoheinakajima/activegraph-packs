"""Configuration for the activity-normalizer pack."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ActivityNormalizerSettings(BaseModel):
    """Zero-key, deterministic normalizer and replay settings."""

    enabled: bool = True
    artifact_store_dir: str = Field(
        default=".activegraph/replay-artifacts",
        description="Root of the immutable content-addressed replay artifact store.",
    )
    max_replay_payload_bytes: int = Field(
        default=8 * 1024 * 1024,
        ge=1,
        le=256 * 1024 * 1024,
        description="Maximum replay artifact read size for one evidence item.",
    )
    default_extractor_id: str = "activity.structure"
    default_extractor_version: str = "0.1.0"
    default_extraction_config_id: str = "default"
    max_normalized_content_chars: int = Field(
        default=16_000,
        ge=128,
        le=1_000_000,
        description="Maximum derived reasoning content retained in one evidence revision.",
    )
    max_candidates_per_evidence: int = Field(default=32, ge=1, le=1000)
    max_candidate_chars: int = Field(default=2000, ge=32, le=100_000)
    retention_policy: str = "source_default"
    encoding: str = "utf-8"
    emit_custom_events: bool = True

    # -- shared-extraction migration (ADR 0026 steps 2-3) ---------------
    legacy_extraction_enabled: bool = Field(
        default=False,
        description=(
            "Re-enable the direct evidence→candidate write path. OFF by "
            "default: extraction now runs on the shared annotation layer "
            "and the compatibility projectors mint the same candidates "
            "from annotations (no long legacy window, D041)."
        ),
    )
    select_shared_extraction: bool = Field(
        default=True,
        description=(
            "When the shared layer seeds its extraction_profile, mint the "
            "next version routing the activity.* structure facets to "
            "activity.structure@0.2.0 so ingestion candidates flow "
            "annotation-first."
        ),
    )
    compat_candidate_projectors: bool = Field(
        default=True,
        description=(
            "Project the legacy candidate types (memory/preference/task/"
            "profile/skill/eval) from activity.* annotations, with the "
            "legacy identity scheme for cross-boundary idempotency."
        ),
    )


__all__ = ["ActivityNormalizerSettings"]
