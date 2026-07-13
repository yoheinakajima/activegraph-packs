"""Neutral connector control-plane materializations.

Service/domain runs remain authoritative. These objects are idempotent adapter
outputs that let every client consume one status, work, learning, and native
shape vocabulary without parsing a provider payload.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from activegraph.packs import ObjectType
from pydantic import BaseModel, ConfigDict, Field

from .contracts import ConnectorFamily, NativeViewState


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectorSurfaceBinding(_StrictModel):
    binding_identity: str = Field(min_length=1)
    source_surface_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    account_ref: str = Field(min_length=1)
    family: ConnectorFamily
    routes: list[str] = Field(default_factory=list)
    active_route: str = Field(min_length=1)
    domain_run_type: str = Field(min_length=1)
    native_shape_version: int = Field(default=1, ge=1)
    maintenance_mode: Literal["none", "manual", "foreground_poll", "webhook", "external"] = "none"
    manual_refresh_available: bool = False
    status: Literal["active", "stale", "revoked"] = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)


class CursorSummary(_StrictModel):
    position_kind: Optional[str] = None
    has_position: bool = False
    advanced: bool = False
    coverage: Literal["unknown", "bounded", "current"] = "unknown"


class ConnectorRunObservation(_StrictModel):
    observation_identity: str = Field(min_length=1)
    domain_run_id: str = Field(min_length=1)
    source_surface_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    account_ref: str = Field(min_length=1)
    family: ConnectorFamily
    route: str = Field(min_length=1)
    state: Literal["queued", "running", "succeeded", "partial", "failed"]
    health: Literal["connected", "working", "current", "partial", "stale", "failed"]
    phase: str = Field(min_length=1)
    mode: str = Field(min_length=1)
    bounds: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    cursor: CursorSummary = Field(default_factory=CursorSummary)
    attempt_event_ids: list[str] = Field(default_factory=list)
    update_event_ids: list[str] = Field(default_factory=list)
    terminal_event_ids: list[str] = Field(default_factory=list)
    success_event_ids: list[str] = Field(default_factory=list)
    maintenance_mode: Literal["none", "manual", "foreground_poll", "webhook", "external"] = "none"
    manual_refresh_available: bool = False
    next_sync_available: bool = False
    error_code: Optional[str] = None
    error: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConnectorLearningDelta(_StrictModel):
    delta_identity: str = Field(min_length=1)
    domain_run_id: str = Field(min_length=1)
    source_surface_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    family: ConnectorFamily
    status: Literal["collecting", "complete", "partial", "failed"] = "collecting"
    evidence: dict[str, int] = Field(default_factory=dict)
    annotation_coverage: dict[str, int] = Field(default_factory=dict)
    resolutions: dict[str, int] = Field(default_factory=dict)
    candidates: dict[str, dict[str, int]] = Field(default_factory=dict)
    failures: int = Field(default=0, ge=0)
    exceptions: list[dict[str, Any]] = Field(default_factory=list)
    cost: dict[str, Any] = Field(default_factory=dict)
    refs: list[str] = Field(default_factory=list)
    # Planned-vs-actual against the approved ingestion plan the run executed
    # (ADR 0039). Empty for runs that carry no plan (maintenance polls).
    plan: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConnectorNativeView(_StrictModel):
    view_identity: str = Field(min_length=1)
    source_surface_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    family: ConnectorFamily
    shape_version: int = Field(default=1, ge=1)
    state: NativeViewState
    data: dict[str, Any] = Field(default_factory=dict)
    refs: list[str] = Field(default_factory=list)
    service_extensions: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class ConnectorOperationalPolicy(_StrictModel):
    policy_identity: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    status: Literal["active", "superseded"] = "active"
    fixture_items: int = Field(default=250, ge=1)
    # Ceilings for one bounded acquisition run. Ingestion plans validate their
    # caps against the active policy; raising a plan bound past these is an
    # explicit policy escalation, never a silent edit (ADR 0039).
    max_acquisition_items: int = Field(default=250, ge=1)
    max_acquisition_pages: int = Field(default=10, ge=1)
    ack_latency_ms: int = Field(default=1_000, ge=1)
    first_progress_ms: int = Field(default=2_000, ge=1)
    projection_read_p95_ms: int = Field(default=500, ge=1)
    max_unyielded_ms: int = Field(default=500, ge=1)
    max_events_per_evidence: int = Field(default=100, ge=1)
    max_annotations_per_evidence: int = Field(default=20, ge=1)
    max_behavior_firings_per_evidence: int = Field(default=25, ge=1)
    max_provider_calls: int = Field(default=10, ge=0)
    max_artifact_bytes: int = Field(default=64 * 1024 * 1024, ge=0)
    max_queue_depth: int = Field(default=5_000, ge=1)
    rationale: str = Field(min_length=1)


PlanStatus = Literal[
    "proposed", "superseded", "approved", "executing", "fulfilled", "abandoned"
]
PlanVerdict = Literal["approved_as_proposed", "edited", "abandoned"]
PlanDerivationBasis = Literal[
    "measured_topology", "volume_only", "service_default", "unknown_topology"
]


class PlanWindow(_StrictModel):
    """The proposed acquisition window. Bounds live in caps, not here."""

    kind: Literal["recent_days", "recent_items"] = "recent_days"
    days: Optional[int] = Field(default=None, ge=1)
    estimated_items: Optional[int] = Field(default=None, ge=0)


class PlanDerivation(_StrictModel):
    """How the window was derived; honest ignorance is a first-class basis."""

    basis: PlanDerivationBasis
    summary: str = Field(min_length=1)
    measurements: dict[str, Any] = Field(default_factory=dict)
    provenance: list[str] = Field(default_factory=list)


class PlanSurface(_StrictModel):
    """One included/excluded source container with its learning expectation."""

    surface_ref: str = Field(min_length=1)
    label: str = ""
    included: bool = True
    expectation: dict[str, Any] = Field(default_factory=dict)


class PlanCaps(_StrictModel):
    """The run bounds and the versioned policy they answer to."""

    max_items: int = Field(ge=1)
    max_pages: int = Field(ge=1)
    page_size: Optional[int] = Field(default=None, ge=1)
    policy_id: str = Field(min_length=1)
    policy_version: int = Field(ge=1)
    ceiling_items: int = Field(ge=1)
    ceiling_pages: int = Field(ge=1)


class ConnectorIngestionPlan(_StrictModel):
    """One versioned, receipted acquisition proposal (ADR 0039 / D059).

    Versions of one series supersede each other (ADR 0020 semantics): a
    superseded version can never execute. The proposal records its acceptance
    prediction before any owner verdict exists (ADR 0018).
    """

    plan_identity: str = Field(min_length=1)
    plan_series: str = Field(min_length=1)
    version: int = Field(ge=1)
    status: PlanStatus = "proposed"
    source_surface_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    account_ref: str = Field(min_length=1)
    family: ConnectorFamily
    purpose: Literal["initial_backfill", "extension"] = "initial_backfill"
    window: PlanWindow
    derivation: PlanDerivation
    surfaces: list[PlanSurface] = Field(default_factory=list)
    caps: PlanCaps
    interpretation_stages: list[str] = Field(default_factory=list)
    predicted_verdict: PlanVerdict
    predicted_confidence_percent: int = Field(ge=0, le=100)
    prediction_basis: dict[str, Any] = Field(default_factory=dict)
    verdict: Optional[PlanVerdict] = None
    verdict_actor: Optional[str] = None
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None
    proposed_by: str = Field(min_length=1)
    approved_by: Optional[str] = None
    domain_run_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConnectorMaintenanceRequest(_StrictModel):
    request_identity: str = Field(min_length=1)
    source_surface_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    account_ref: str = Field(min_length=1)
    kind: Literal["manual_refresh"] = "manual_refresh"
    requested_by: str = Field(min_length=1)
    status: Literal["proposed", "accepted", "failed", "rejected"] = "proposed"
    domain_run_id: Optional[str] = None
    error: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


OBJECT_TYPES = [
    ObjectType("connector_surface_binding", ConnectorSurfaceBinding, "A service surface bound to one connector family."),
    ObjectType("connector_run_observation", ConnectorRunObservation, "A neutral adapter view of an authoritative domain run."),
    ObjectType("connector_learning_delta", ConnectorLearningDelta, "Run-scoped counts and references for derived learning."),
    ObjectType("connector_native_view", ConnectorNativeView, "A validated family-native read projection materialization."),
    ObjectType("connector_operational_policy", ConnectorOperationalPolicy, "Versioned release budgets for connector work."),
    ObjectType("connector_maintenance_request", ConnectorMaintenanceRequest, "A provider-neutral owner request for bounded connector maintenance."),
    ObjectType("connector_ingestion_plan", ConnectorIngestionPlan, "A versioned, receipted acquisition proposal bound to its run."),
]

RELATION_TYPES = []

__all__ = [
    "ConnectorSurfaceBinding",
    "ConnectorRunObservation",
    "ConnectorLearningDelta",
    "ConnectorNativeView",
    "ConnectorOperationalPolicy",
    "ConnectorIngestionPlan",
    "PlanWindow",
    "PlanDerivation",
    "PlanSurface",
    "PlanCaps",
    "PlanStatus",
    "PlanVerdict",
    "OBJECT_TYPES",
    "RELATION_TYPES",
]
