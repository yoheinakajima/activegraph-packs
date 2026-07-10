"""Settings for canonical outcome capture and reliability projection."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EvalOutcomeSettings(BaseModel):
    """Deterministic reliability effects; all values are separately queryable."""

    enabled: bool = True
    supported_retrieval_multiplier: float = Field(default=1.0, ge=0.0, le=1.0)
    weak_retrieval_multiplier: float = Field(default=0.75, ge=0.0, le=1.0)
    harmful_retrieval_multiplier: float = Field(default=0.1, ge=0.0, le=1.0)
    stale_retrieval_multiplier: float = Field(default=0.25, ge=0.0, le=1.0)
