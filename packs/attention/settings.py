"""Bounds for semantic attention batches."""

from pydantic import BaseModel, Field


class AttentionSettings(BaseModel):
    max_observations_per_batch: int = Field(default=100, ge=1, le=1_000)


__all__ = ["AttentionSettings"]
