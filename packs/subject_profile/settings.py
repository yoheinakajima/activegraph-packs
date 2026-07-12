from pydantic import BaseModel, Field


class SubjectProfileSettings(BaseModel):
    owner_subject_ref: str = Field(default="owner")
    confirmed_trust: float = Field(default=0.9, ge=0.0, le=1.0)

