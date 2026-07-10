"""Bounds for assistant self-summary ingestion."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AssistantSelfSummarySettings(BaseModel):
    max_summary_chars: int = Field(default=64_000, ge=100, le=1_000_000)
    max_normalized_chars: int = Field(default=32_000, ge=100, le=1_000_000)


__all__ = ["AssistantSelfSummarySettings"]
