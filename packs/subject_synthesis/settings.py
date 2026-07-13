from typing import Optional

from pydantic import BaseModel, Field


class SubjectSynthesisSettings(BaseModel):
    subject_ref: str = Field(default="owner")
    # Bounds keep the pass a taste, never a meal (ADR 0042 posture).
    max_identity_candidates: int = Field(default=8, ge=0, le=24)
    max_project_candidates: int = Field(default=7, ge=0, le=24)
    max_input_facts: int = Field(default=48, ge=1, le=200)
    max_input_labels: int = Field(default=48, ge=0, le=200)
    max_input_entities: int = Field(default=24, ge=0, le=100)
    timeout_seconds: float = Field(default=120.0, gt=0)
    model: Optional[str] = None
    # Identity attributes synthesis may propose. Never email (an address in
    # a proposal is a disclosure the owner didn't make) and never name
    # (single-valued, owner-seeded).
    identity_attributes: list[str] = Field(
        default_factory=lambda: [
            "company", "organization", "project", "affiliation",
            "person", "role", "location", "handle", "url",
        ]
    )
