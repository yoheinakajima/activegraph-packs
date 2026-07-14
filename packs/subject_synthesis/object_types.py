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


class ComprehensionRequest(_StrictModel):
    """One staged-reduction campaign over a connector recipe (ADR 0045 §3–4).

    Selection ran at request time; item identity, exclusions, and coverage
    are recorded here; batches settle through the host pump.
    """

    request_identity: str = Field(min_length=1)
    recipe_id: str = Field(min_length=1)
    service: str = ""
    source_surface_id: str = Field(min_length=1)
    plan_identity: str = ""
    status: Literal[
        "proposed", "reducing", "aggregating", "completed", "failed"
    ] = "proposed"
    requested_by: str = Field(min_length=1)
    counts: dict[str, int] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
    item_refs: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceItemSummary(_StrictModel):
    """One structured leaf row for one source item, evidence refs mandatory.

    The fast model summarized and extracted; it never promotes — the refs
    come from the prepared payload by construction, never from the model.
    """

    summary_identity: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    recipe_id: str = Field(min_length=1)
    item_ref: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    batch_index: int = Field(default=0, ge=0)
    fields: dict[str, Any] = Field(default_factory=dict)
    model: Optional[str] = None
    injection_flags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ComprehensionAggregate(_StrictModel):
    """A bounded middle reduction over one group of leaf rows."""

    aggregate_identity: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    recipe_id: str = Field(min_length=1)
    group_key: str = Field(min_length=1)
    summary: str = ""
    key_people: list[str] = Field(default_factory=list)
    key_decisions: list[str] = Field(default_factory=list)
    instruction_candidates: list[str] = Field(default_factory=list)
    leaf_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    model: Optional[str] = None
    injection_flags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SetupDraft(_StrictModel):
    """One versioned, editable proposal of the owner's world (ADR 0046).

    A proposal container with no authority: submission or explicit deferral
    is the terminal onboarding gate, a new run mints a new version, and a
    submitted version is immutable history.
    """

    draft_identity: str = Field(min_length=1)
    version: int = Field(ge=1)
    subject_ref: str = "owner"
    status: Literal[
        "proposed", "submitting", "submitted", "partial", "deferred", "superseded"
    ] = "proposed"
    source: Literal["synthesis", "deterministic"] = "synthesis"
    run_id: Optional[str] = None
    supersedes: Optional[str] = None
    included_refs: list[str] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SetupDraftItem(_StrictModel):
    """One routed draft item with its pre-recorded prediction and verdict."""

    item_identity: str = Field(min_length=1)
    draft_id: str = Field(min_length=1)
    section: Literal[
        "identity", "narrative", "instructions", "projects", "people", "access"
    ]
    destination: Literal[
        "subject_profile", "instructions", "projects",
        "entity_relationship", "access_hint",
    ]
    proposed: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    evidence_refs: list[str] = Field(min_length=1)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    uncertainty: str = ""
    predicted_verdict: Literal["accept", "reject", "edit", "defer"] = "accept"
    predicted_confidence_percent: int = Field(default=25, ge=0, le=100)
    prediction_basis: dict[str, Any] = Field(default_factory=dict)
    verdict: Optional[Literal["accept", "reject", "edit", "defer"]] = None
    verdict_actor: Optional[str] = None
    edited_value: Optional[dict[str, Any]] = None
    status: Literal[
        "proposed", "accepted", "rejected", "edited", "deferred",
        "superseded", "committed", "commit_failed",
    ] = "proposed"
    candidate_ref: Optional[str] = None
    commit_error: Optional[str] = None
    injection_flags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class InformationAccessHint(_StrictModel):
    """An accepted information-access strategy: which source/pattern serves a
    class of future question. A hint for the query resolver — never promoted
    memory or identity (ADR 0046 §2)."""

    hint_identity: str = Field(min_length=1)
    subject_ref: str = "owner"
    question_class: str = ""
    source: str = Field(min_length=1)
    strategy: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    accepted_by: str = Field(min_length=1)
    draft_item_id: Optional[str] = None
    status: Literal["active", "retired"] = "active"
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
    ObjectType(
        "comprehension_request",
        ComprehensionRequest,
        "One staged-reduction campaign over a declared connector recipe.",
    ),
    ObjectType(
        "source_item_summary",
        SourceItemSummary,
        "One structured, evidence-cited leaf row for one source item.",
    ),
    ObjectType(
        "comprehension_aggregate",
        ComprehensionAggregate,
        "A bounded middle reduction over one group of leaf summaries.",
    ),
    ObjectType(
        "setup_draft",
        SetupDraft,
        "One versioned, editable setup-draft proposal of the owner's world.",
    ),
    ObjectType(
        "setup_draft_item",
        SetupDraftItem,
        "One routed setup-draft item with prediction, verdict, and refs.",
    ),
    ObjectType(
        "information_access_hint",
        InformationAccessHint,
        "An accepted information-access strategy for a class of questions.",
    ),
]

__all__ = [
    "SubjectSynthesisRequest",
    "SubjectSynthesisRun",
    "ComprehensionRequest",
    "SourceItemSummary",
    "ComprehensionAggregate",
    "SetupDraft",
    "SetupDraftItem",
    "InformationAccessHint",
    "OBJECT_TYPES",
]
