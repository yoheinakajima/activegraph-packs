from pydantic import BaseModel, Field


class SubjectProfileSettings(BaseModel):
    owner_subject_ref: str = Field(default="owner")
    confirmed_trust: float = Field(default=0.9, ge=0.0, le=1.0)
    # Contradiction is only meaningful where one value is expected. Every
    # other attribute is set-valued and accumulates — a second confirmed
    # handle, url, project, or company is more identity, not a conflict.
    single_valued_attributes: list[str] = Field(
        default_factory=lambda: ["name"]
    )
    # ADR 0043: attributes carry a class. Identity anchors interpretation
    # and headlines recognition; instructions belong in the behavior
    # surface; anything unlisted is narrative — the subject's own words.
    attribute_classes: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "identity": [
                "name", "handle", "url", "email", "company", "organization",
                "affiliation", "project", "person", "relationship", "role",
                "location",
            ],
            "instruction": ["preference", "communication_style", "instruction"],
        }
    )
    # Confirmed facts about who matters (never identity aliases) may seed
    # importance as explicit owner acts (ADR 0038 rule 3 / ADR 0039).
    importance_seed_attributes: list[str] = Field(
        default_factory=lambda: [
            "relationship", "company", "organization", "person", "project",
            "affiliation",
        ]
    )
    importance_seed_strength_milli: int = Field(default=600, ge=0, le=1_000)

