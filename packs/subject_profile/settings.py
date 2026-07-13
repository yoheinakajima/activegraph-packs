from pydantic import BaseModel, Field


class SubjectProfileSettings(BaseModel):
    owner_subject_ref: str = Field(default="owner")
    confirmed_trust: float = Field(default=0.9, ge=0.0, le=1.0)
    # Confirmed facts about who matters (never identity aliases) may seed
    # importance as explicit owner acts (ADR 0038 rule 3 / ADR 0039).
    importance_seed_attributes: list[str] = Field(
        default_factory=lambda: [
            "relationship", "company", "organization", "person", "project",
            "affiliation",
        ]
    )
    importance_seed_strength_milli: int = Field(default=600, ge=0, le=1_000)

