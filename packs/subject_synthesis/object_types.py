"""Durable work unit and receipt for the synthesis pass."""

from __future__ import annotations

from typing import Any, Literal, Optional

from activegraph.packs import ObjectType
from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SubjectSynthesisRequest(_StrictModel):
    """One requested synthesis pass; hosts settle it on their pump."""

    request_identity: str = Field(min_length=1)
    subject_ref: str = "owner"
    reason: str = ""
    status: Literal["proposed", "completed", "failed"] = "proposed"
    run_id: Optional[str] = None
    error: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubjectSynthesisRun(_StrictModel):
    """The receipt: what was read, what was proposed, what was set aside."""

    run_identity: str = Field(min_length=1)
    subject_ref: str = "owner"
    status: Literal["completed", "failed"] = "completed"
    model: Optional[str] = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    proposed: dict[str, Any] = Field(default_factory=dict)
    # What synthesis deliberately did NOT propose (e.g. tool-usage labels),
    # each with its reason — observability for "why isn't X a project?".
    noise: list[dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


OBJECT_TYPES = [
    ObjectType(
        "subject_synthesis_request",
        SubjectSynthesisRequest,
        "A requested comprehension-synthesis pass awaiting host settlement.",
    ),
    ObjectType(
        "subject_synthesis_run",
        SubjectSynthesisRun,
        "The receipt of one synthesis pass: inputs, proposals, noise, error.",
    ),
]

__all__ = ["SubjectSynthesisRequest", "SubjectSynthesisRun", "OBJECT_TYPES"]
