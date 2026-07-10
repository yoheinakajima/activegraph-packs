"""Settings for governed learned skill artifacts."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SkillsSettings(BaseModel):
    """Zero-key settings for the skills lifecycle."""

    enabled: bool = True
    allow_candidate_trials: bool = Field(
        default=True,
        description="Allow candidate versions in trial runs; real runs require promotion.",
    )
    default_candidate_version: str = Field(
        default="0.1.0",
        pattern=r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$",
    )
