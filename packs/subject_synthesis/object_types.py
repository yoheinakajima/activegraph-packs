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
    model_role: str = "fast"
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
    model_role: str = "balanced"
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
    #: "successor" marks a post-acceptance review batch promoted from an
    #: understanding delta that targeted a submitted predecessor (ADR 0051 §3).
    source: Literal["synthesis", "deterministic", "successor"] = "synthesis"
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
    #: The typed correction the owner attached to a rejection (ADR 0048 §4):
    #: a distinct teaching signal, never a bare binary no.
    correction: Optional[Literal[
        "not_me", "duplicate", "incorrect", "not_useful",
        "wrong_type", "wrong_grouping",
    ]] = None
    #: Owner comments: durable owner evidence that supersedes nothing and
    #: never counts as a correct system prediction.
    comments: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal[
        "proposed", "accepted", "rejected", "edited", "deferred",
        "superseded", "committed", "commit_failed",
    ] = "proposed"
    candidate_ref: Optional[str] = None
    commit_error: Optional[str] = None
    injection_flags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UnderstandingDelta(_StrictModel):
    """New or changed comprehension after a review snapshot froze
    (ADR 0048 §3): an additive, evidence-linked diff the owner applies,
    dismisses, or defers. It never replaces reviewed items, never re-keys
    them, and never reopens resolved onboarding."""

    delta_identity: str = Field(min_length=1)
    subject_ref: str = "owner"
    draft_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    #: The closed disposition vocabulary (ADR 0051 §1): open = unresolved
    #: owner work (blocks pre-Hatch completion); applied = staged into the
    #: active review as UNDECIDED proposals (staging is never acceptance);
    #: dismissed = explicit "not needed"; deferred = explicit route to
    #: Mission Control (stays visible, never counted reviewed); superseded =
    #: replaced by a newer cumulative delta.
    status: Literal[
        "open", "applied", "dismissed", "deferred", "superseded"
    ] = "open"
    source: Literal["synthesis", "deterministic"] = "deterministic"
    run_id: Optional[str] = None
    #: Each row: change (new|changed|conflicting), semantic_key, section,
    #: proposed, rationale, evidence_refs, confidence, uncertainty,
    #: predecessor_item_id (for changed/conflicting).
    items: list[dict[str, Any]] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    resolved_by: str = ""
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


class SourceLens(_StrictModel):
    """One source's versioned view into shared understanding (ADR 0047 §3).

    A lens records what one selected source contributed — coverage, gaps,
    exclusions, uncertainty, and settlement — with every contribution row
    separating ``support_refs`` (evidence this source itself contains) from
    ``context_refs`` (borrowed other-source material that aided selection or
    interpretation). A lens is a projection into the working understanding,
    never a private truth store.
    """

    lens_identity: str = Field(min_length=1)
    affordance_id: str = Field(min_length=1)
    service: str = ""
    source_surface_id: str = Field(min_length=1)
    subject_ref: str = "owner"
    status: Literal[
        "pending", "contributing", "contributed",
        "failed", "declined", "unavailable",
    ] = "pending"
    #: The working-understanding version this lens last read (0 = none).
    working_version_pinned: int = Field(default=0, ge=0)
    contribution_count: int = Field(default=0, ge=0)
    contributions: list[dict[str, Any]] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    gaps: list[str] = Field(default_factory=list)
    exclusions: dict[str, Any] = Field(default_factory=dict)
    uncertainties: list[str] = Field(default_factory=list)
    terminal_reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkingUnderstanding(_StrictModel):
    """One versioned, non-canonical cross-source snapshot (ADR 0047 §3–4).

    The coordinator reads this packet; lenses pin the version they consumed.
    Entries carry an authority class — only owner-confirmed/promoted material
    may guide an approved outward query. Corroboration counts independent
    support lineage only; borrowed context never strengthens a hypothesis.
    """

    working_identity: str = Field(min_length=1)
    version: int = Field(ge=1)
    subject_ref: str = "owner"
    entries: list[dict[str, Any]] = Field(default_factory=list)
    unresolved: list[dict[str, Any]] = Field(default_factory=list)
    source_coverage: dict[str, Any] = Field(default_factory=dict)
    organization_candidates: list[dict[str, Any]] = Field(default_factory=list)
    pins: dict[str, Any] = Field(default_factory=dict)
    predecessor_ref: Optional[str] = None
    content_hash: str = Field(min_length=1)
    changed_kinds: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReinterpretationRequest(_StrictModel):
    """Targeted, version-pinned reinterpretation of one downstream step
    (ADR 0047 §3). Minted when the working understanding materially changes;
    never a global rerun, always durable visible work with a successor."""

    request_identity: str = Field(min_length=1)
    working_version: int = Field(ge=1)
    target_kind: Literal["synthesis", "lens_alignment", "draft_recompose"]
    target_ref: str = ""
    reason: str = Field(min_length=1)
    status: Literal["proposed", "completed", "failed", "skipped"] = "proposed"
    successor_ref: Optional[str] = None
    error: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ComprehensionCampaign(_StrictModel):
    """One governed comprehension campaign (ADR 0047 §1): the coordinator
    proposes bounded moves inside these deterministic rails; the host
    validates and executes. Budgets here are authoritative."""

    campaign_identity: str = Field(min_length=1)
    subject_ref: str = "owner"
    status: Literal[
        "open", "paused_owner", "completed", "failed", "abandoned",
    ] = "open"
    selected_affordances: list[str] = Field(default_factory=list)
    permitted_action_classes: list[str] = Field(default_factory=lambda: ["R0", "R1"])
    budgets: dict[str, Any] = Field(default_factory=dict)
    spent: dict[str, Any] = Field(default_factory=dict)
    working_version: int = Field(default=0, ge=0)
    move_count: int = Field(default=0, ge=0)
    stop_reason: str = ""
    pins: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CoordinatorMove(_StrictModel):
    """One proposed campaign move and its full governance record
    (ADR 0047 §1). The structured rationale is the audit surface; private
    chain-of-thought is never stored. The model's recommendation is never
    the authorization — ``validation`` records the deterministic verdict."""

    move_identity: str = Field(min_length=1)
    campaign_ref: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    kind: Literal[
        "inspect_source", "outward_query", "reduce_fast", "drill_down",
        "align_entities", "ask_owner", "propose_amendment",
        "synthesize", "stop",
    ]
    affordance_id: str = ""
    capability: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    working_version: int = Field(default=0, ge=0)
    support_refs: list[str] = Field(default_factory=list)
    context_refs: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    expected_gain: str = ""
    cost: dict[str, Any] = Field(default_factory=dict)
    requires_owner: bool = False
    success_condition: str = ""
    status: Literal[
        "proposed", "rejected", "paused", "approved",
        "executing", "committed", "failed",
    ] = "proposed"
    validation: dict[str, Any] = Field(default_factory=dict)
    proposer: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OwnerQuestion(_StrictModel):
    """A bounded differentiating question the campaign asks the owner
    (ADR 0047 §5) — an owner-decision boundary, not a workflow stage."""

    question_identity: str = Field(min_length=1)
    campaign_ref: str = Field(min_length=1)
    move_ref: str = ""
    kind: Literal[
        "identity_ambiguity", "differentiating", "conflict", "scope",
    ] = "differentiating"
    prompt: str = Field(min_length=1)
    options: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["open", "answered", "dismissed"] = "open"
    answer: dict[str, Any] = Field(default_factory=dict)
    answered_by: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceDrillDown(_StrictModel):
    """One bounded reasoning-model evidence read (ADR 0047 §2): which items
    or excerpts entered reasoning context, why, what it cost, and what came
    back. A whole mailbox can never silently become one of these."""

    drill_identity: str = Field(min_length=1)
    campaign_ref: str = ""
    move_ref: str = ""
    affordance_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    included_refs: list[str] = Field(default_factory=list)
    excluded: dict[str, Any] = Field(default_factory=dict)
    status: Literal["proposed", "performed", "committed", "failed"] = "proposed"
    findings: list[dict[str, Any]] = Field(default_factory=list)
    model: Optional[str] = None
    model_role: str = "reasoning"
    cost: dict[str, Any] = Field(default_factory=dict)
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
    ObjectType(
        "understanding_delta",
        UnderstandingDelta,
        "An additive diff of new/changed comprehension after a review froze.",
    ),
    ObjectType(
        "source_lens",
        SourceLens,
        "One source's versioned lens into the shared working understanding.",
    ),
    ObjectType(
        "working_understanding",
        WorkingUnderstanding,
        "One versioned cross-source working-understanding snapshot.",
    ),
    ObjectType(
        "reinterpretation_request",
        ReinterpretationRequest,
        "A targeted, version-pinned reinterpretation of one downstream step.",
    ),
    ObjectType(
        "comprehension_campaign",
        ComprehensionCampaign,
        "One governed comprehension campaign with authoritative budgets.",
    ),
    ObjectType(
        "coordinator_move",
        CoordinatorMove,
        "One proposed campaign move with its deterministic validation record.",
    ),
    ObjectType(
        "owner_question",
        OwnerQuestion,
        "A bounded differentiating question the campaign asks the owner.",
    ),
    ObjectType(
        "evidence_drill_down",
        EvidenceDrillDown,
        "One bounded, recorded reasoning-model evidence read.",
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
    "UnderstandingDelta",
    "SourceLens",
    "WorkingUnderstanding",
    "ReinterpretationRequest",
    "ComprehensionCampaign",
    "CoordinatorMove",
    "OwnerQuestion",
    "EvidenceDrillDown",
    "OBJECT_TYPES",
]
