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
    # Campaign coordination defaults (ADR 0047 §1). Authoritative budgets a
    # campaign opens with unless the host passes explicit ones; a move that
    # would exceed them pauses as an amendment or is rejected.
    campaign_max_moves: int = Field(default=24, ge=1, le=200)
    campaign_max_tokens: int = Field(default=200_000, ge=1_000)
    campaign_max_cost_milli: int = Field(default=5_000, ge=0)
    campaign_max_seconds: float = Field(default=3_600.0, gt=0)
    coordinator_timeout_seconds: float = Field(default=120.0, gt=0)
    drill_down_max_items: int = Field(default=6, ge=1, le=24)
    drill_down_max_excerpt_chars: int = Field(default=2_000, ge=100)
    drill_down_max_context_tokens: int = Field(default=6_000, ge=500)
