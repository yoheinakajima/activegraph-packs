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


__all__ = ["ActivityNormalizerSettings"]
