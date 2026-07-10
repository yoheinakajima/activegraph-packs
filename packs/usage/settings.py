"""Deterministic defaults for neutral usage projections."""

from __future__ import annotations

from pydantic import BaseModel, Field


class UsageSettings(BaseModel):
    enabled: bool = True
    default_gate_id: str = "usage.category.default"
    default_gate_version: int = Field(default=1, ge=1)
    min_unique_events: int = Field(default=25, ge=1)
    min_coverage_days: int = Field(default=3, ge=1)
    allow_either: bool = True


__all__ = ["UsageSettings"]
