"""Tool Gateway Pack object and relation types — v0.1.

All external capability calls (APIs, MCP, local tools, SDK clients)
must flow through this pack. It normalizes calls, records actions,
runs policy checks, and maps results back to Core source objects.

Key design rules:
- credential_ref stores a NAME only — never an actual secret value
- input_data is recorded as-is (secrets must be absent before recording)
- CapabilityResult.output_data is sanitized and size-limited
- CapabilityApproval is the trigger for call_executor — fully graph-visible
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from activegraph.packs import ObjectType, RelationType


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntegrationRoute(_StrictModel):
    """One transport route serving a canonical service/account profile."""

    path: Literal["export", "mcp", "composio", "native", "local", "pack", "manual"]
    route_ref: str = Field(min_length=1)
    status: Literal["active", "pending", "stale", "revoked", "failed"] = "active"
    connected_account_id: Optional[str] = None
    schema_version: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntegrationCapability(_StrictModel):
    """Conservatively classified canonical (service, operation) capability."""

    operation: str = Field(min_length=1)
    action_class: Literal["R0", "R1", "R2", "R3", "R4"]
    classification_source: Literal["default", "operator", "evidence"] = "default"
    route: str = Field(min_length=1)
    provider_operation: Optional[str] = None
    input_schema_fingerprint: Optional[str] = None
    idempotency: Literal["none", "client_guard", "provider", "natural"] = "none"
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntegrationSignal(_StrictModel):
    """A profile surface's predicted downstream yield, never a truth claim.

    Richness is measured or honestly absent (ADR 0039): ``unmeasured`` marks a
    surface the probes said nothing about, and a numeric confidence may only
    accompany a measurement carried with provenance references.
    """

    surface: str = Field(min_length=1)
    candidate_types: list[str] = Field(default_factory=list)
    estimated_richness: Literal[
        "unknown", "unmeasured", "low", "medium", "high"
    ] = "unknown"
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    provenance: list[str] = Field(default_factory=list)
    measurement: dict[str, Any] = Field(default_factory=dict)


class IntegrationClaim(_StrictModel):
    """One inspectable profile claim with epistemic state of its own."""

    claim_key: str = Field(min_length=1)
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    freshness: Literal["current", "stale", "unknown"] = "unknown"
    provenance: list[str] = Field(default_factory=list)
    classification_source: Literal["default", "operator", "evidence"] = "evidence"
    asserted_by: str = Field(min_length=1)
    observed_at_event_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AggregatorProfile(_StrictModel):
    """Thin profile for a route provider; it never inventories the catalog."""

    profile_identity: str = Field(min_length=1)
    aggregator: str = Field(min_length=1)
    user_ref: str = Field(min_length=1)
    auth_state: Literal["unconfigured", "configured", "pending", "active", "failed"]
    available_services: list[str] = Field(default_factory=list)
    enabled_services: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntegrationProfile(_StrictModel):
    """Versioned understanding of one canonical (service, account)."""

    profile_identity: str = Field(min_length=1)
    profile_version: int = Field(default=1, ge=1)
    service: str = Field(min_length=1)
    account_ref: str = Field(min_length=1)
    account_display: Optional[str] = None
    status: Literal["active", "superseded", "stale", "revoked", "failed"] = "active"
    routes: list[IntegrationRoute] = Field(default_factory=list)
    scopes_granted: list[str] = Field(default_factory=list)
    scopes_available: list[str] = Field(default_factory=list)
    facets: list[Literal["record_store", "effector", "social_graph", "utility"]] = Field(default_factory=list)
    capability_inventory: list[IntegrationCapability] = Field(default_factory=list)
    data_topology: dict[str, Any] = Field(default_factory=dict)
    signal_map: list[IntegrationSignal] = Field(default_factory=list)
    claims: list[IntegrationClaim] = Field(default_factory=list)
    health: dict[str, Any] = Field(default_factory=dict)
    exploration_receipts: list[str] = Field(default_factory=list)
    supersedes_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntegrationExploration(_StrictModel):
    """Budgeted R0 probe receipt used to build or refresh a profile."""

    receipt_identity: str = Field(min_length=1)
    service: str = Field(min_length=1)
    account_ref: str = Field(min_length=1)
    route: str = Field(min_length=1)
    profile_id: Optional[str] = None
    probe_call_ids: list[str] = Field(default_factory=list)
    budget: int = Field(ge=1)
    structural_only: bool = True
    shape_fingerprint: Optional[str] = None
    status: Literal["proposed", "completed", "partial", "failed"] = "proposed"
    injection_flags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ================================================================ Schemas


class CapabilityProvider(BaseModel):
    """A registered external capability provider.

    Examples: OpenAI API, a local Python function, an MCP server,
    a REST API, an SDK client.

    Providers are registered once and referenced by ID in CapabilityCall.
    """

    name: str = Field(description="Human-readable name (e.g. 'OpenAI', 'CRM API').")
    kind: Literal["local", "api", "mcp", "sdk", "webhook"] = Field(
        description="Category of provider."
    )
    base_url: Optional[str] = Field(
        default=None,
        description="Base URL for API/MCP providers.",
    )
    description: str = Field(
        default="",
        description="What this provider enables.",
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description="List of capability names this provider exposes.",
    )
    credential_ref_name: Optional[str] = Field(
        default=None,
        description=(
            "Name of the CredentialRef to use for this provider. "
            "Secrets Pack resolves this to an actual secret at call time."
        ),
    )
    enabled: bool = Field(default=True)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityCall(BaseModel):
    """A proposed or executing capability call.

    Lifecycle:
      proposed → policy_checking → approved → executing → done | failed | rejected

    IMPORTANT: input_data must NOT contain actual secrets — use
    credential_ref_name to reference credentials by name only.
    """

    provider_id: str = Field(
        description="ID of the CapabilityProvider object.",
    )
    provider_name: str = Field(
        default="",
        description="Denormalized provider name for easy display.",
    )
    capability_name: str = Field(
        description="Name of the specific capability/method to call.",
    )
    input_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Input parameters. Must not contain secrets.",
    )
    credential_ref_name: Optional[str] = Field(
        default=None,
        description="Name of credential reference (resolved by Secrets Pack at runtime).",
    )
    credential_ref_id: Optional[str] = Field(
        default=None,
        description=(
            "ID of the CredentialRef graph object. Required for call_executor to "
            "resolve and inject credentials via Secrets Pack and record the usage event."
        ),
    )
    risk_class: Literal["low", "medium", "high", "critical"] = Field(
        default="medium",
        description=(
            "LEGACY risk classification for policy gating. "
            "low=read-only/safe, medium=writes to external systems, "
            "high=financial/legal consequences, critical=irreversible. "
            "Independent of action_class; never mapped to or from it."
        ),
    )
    action_class: Literal["", "R0", "R1", "R2", "R3", "R4"] = Field(
        default="",
        description=(
            "Canonical consequence class (ADR 0016): R0=observe, R1=derive, "
            "R2=reversible action, R3=irreversible/outward, R4=governance. "
            "'' means undeclared — the call is then ineligible for the "
            "action-class dimension's auto-approval (fails closed to hold). "
            "Never inferred from risk_class."
        ),
    )
    status: Literal["proposed", "policy_checking", "approved", "rejected", "executing", "done", "failed"] = Field(
        default="proposed",
        description="Call lifecycle status.",
    )
    proposed_by: Optional[str] = Field(
        default=None,
        description="Name of the behavior that proposed this call.",
    )
    frame_id: Optional[str] = Field(default=None)
    proposed_at: Optional[str] = Field(
        default=None,
        description="ISO 8601 datetime when the call was proposed.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityApproval(BaseModel):
    """Records that a capability call was approved for execution.

    Created by policy_enforcer when a call is auto-approved.
    Serves as the graph-visible trigger for call_executor.

    Design: policy_enforcer creates this object → call_executor fires
    on capability_approval.created → executes the call.

    This makes the approval-to-execution chain fully graph-visible:
    every approval has a corresponding CapabilityApproval record.
    """

    call_id: str = Field(
        description="ID of the CapabilityCall that was approved.",
    )
    provider_id: str = Field(default="")
    provider_name: str = Field(default="")
    capability_name: str = Field(default="")
    action_class: Literal["", "R0", "R1", "R2", "R3", "R4"] = Field(
        default="",
        description=(
            "Canonical consequence class of the approved call (ADR 0016); "
            "'' when the capability declares none. Copied from the call so "
            "the approval record itself names what class of action it "
            "authorized."
        ),
    )
    input_data: dict[str, Any] = Field(default_factory=dict)
    credential_ref_name: Optional[str] = Field(default=None)
    credential_ref_id: Optional[str] = Field(
        default=None,
        description="Passed from CapabilityCall so call_executor can resolve credentials.",
    )
    frame_id: Optional[str] = Field(default=None)
    policy_decision: str = Field(
        default="auto_approved",
        description="How the approval decision was made.",
    )
    approver: str = Field(
        default="policy_enforcer",
        description="Name of the behavior or agent that approved the call.",
    )
    approved_at: Optional[str] = Field(
        default=None,
        description="ISO 8601 datetime of approval.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityDenial(BaseModel):
    """Records that a held capability call was explicitly denied.

    Created by the deny_capability tool when an approver refuses a call
    held at status='policy_checking'. Refusals are as auditable as
    grants: the denial, who made it, and why are all graph objects —
    a denied call is never just a status flip.
    """

    call_id: str = Field(
        description="ID of the CapabilityCall that was denied.",
    )
    provider_name: str = Field(default="")
    capability_name: str = Field(default="")
    action_class: Literal["", "R0", "R1", "R2", "R3", "R4"] = Field(
        default="",
        description=(
            "Canonical consequence class of the denied call (ADR 0016); "
            "'' when the capability declares none. Refusals name the class "
            "with the same weight grants do."
        ),
    )
    denier: str = Field(
        default="",
        description="Ref of the principal or operator that denied the call.",
    )
    reason: str = Field(
        default="",
        description="Human-readable reason the call was refused.",
    )
    denied_at: Optional[str] = Field(
        default=None,
        description="ISO 8601 datetime of denial.",
    )
    frame_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityResult(BaseModel):
    """The result of an executed capability call.

    Created by call_executor after a CapabilityCall completes.
    Mapped to a Core source object by result_sourcer so downstream
    behaviors can extract observations from tool outputs.
    """

    call_id: str = Field(description="ID of the CapabilityCall that produced this result.")
    provider_name: str = Field(default="")
    capability_name: str = Field(default="")
    output_data: str = Field(
        default="",
        description=(
            "Serialized output (JSON string or plain text). Size-limited by "
            "ToolGatewaySettings.max_output_chars."
        ),
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if the call failed.",
    )
    success: bool = Field(
        default=True,
        description="True if the call completed without error.",
    )
    executed_at: Optional[str] = Field(
        default=None,
        description="ISO 8601 datetime of execution.",
    )
    sanitized: bool = Field(
        default=False,
        description="True if output was processed to remove sensitive data.",
    )
    untrusted: bool = Field(
        default=True,
        description=(
            "Capability output is external content and carries no authority. "
            "Always True — the field exists so downstream consumers (prompt "
            "assembly, exports) can filter/mark tool-derived content."
        ),
    )
    injection_flags: list[str] = Field(
        default_factory=list,
        description=(
            "Labels of injection patterns matched in the output (see "
            "tool_gateway.untrusted.INJECTION_PATTERNS). Non-empty means an "
            "injection_flag audit object was also created."
        ),
    )
    source_id: Optional[str] = Field(
        default=None,
        description="ID of the Core source object created from this result.",
    )
    frame_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class InjectionFlag(BaseModel):
    """A capability result matched known prompt-injection patterns.

    Flags never block execution (heuristics are tripwires, not oracles) —
    they make the attempt visible: what pattern matched, in which result,
    with an excerpt, so a human or reviewing behavior can act. The result's
    envelope also carries a visible warning (see untrusted.wrap_untrusted).
    """

    call_id: str = Field(description="The capability_call whose output was flagged.")
    result_id: str = Field(default="", description="The capability_result flagged.")
    provider_name: str = Field(default="")
    capability_name: str = Field(default="")
    patterns: list[str] = Field(
        default_factory=list,
        description="Matched pattern labels from untrusted.INJECTION_PATTERNS.",
    )
    excerpt: str = Field(
        default="",
        description="Short sanitized excerpt of the flagged content (for triage).",
    )
    flagged_at: str = Field(default="")
    frame_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolPolicy(BaseModel):
    """A standing-scope tool policy — automation as a promoted prediction.

    ADR 0018's automation stage: sustained, high-confidence prediction
    accuracy for one decision scope (action_class|capability_key)
    generates this governed artifact as a CANDIDATE; explicit owner
    approval promotes it; a promoted policy is what lets the gateway's
    action-class dimension auto-approve the scope's R2 capability within
    the runtime ceiling ("Level 3 permits a policy to auto-approve a
    bounded reversible capability; it does not auto-approve every R2
    capability" — SCORING_CONTRACT). Accuracy degradation demotes it,
    observably and reversibly, naming the missed predictions.

    Versioned and provenance-carrying: the evidence block records the
    exact prediction pairs (refs, verdicts) and the versioned rule that
    generated the candidate, so every automation traces to the
    prediction history that earned it. R3/R4 scopes are structurally
    unrepresentable (action_class is a closed R0-R2 set here).
    """

    policy_id: str = Field(description="Stable id, one per scope_key.")
    policy_version: int = Field(default=1, ge=1)
    scope_key: str = Field(description="'action_class|capability_key'.")
    capability_key: str = Field(description="'provider.capability'.")
    action_class: Literal["R0", "R1", "R2"] = Field(
        description="R3/R4 can never be standing scopes (ADR 0018)."
    )
    status: Literal["candidate", "promoted", "demoted", "disabled"] = "candidate"
    rule_id: str = ""
    rule_version: int = 0
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Prediction history that earned this candidate: pair refs, "
            "counts, accuracy percent, thresholds snapshot."
        ),
    )
    proposed_by: str = ""
    approved_by: str = ""
    promotion_history: list[dict[str, Any]] = Field(default_factory=list)
    demotion_reason: str = ""
    missed_prediction_refs: list[str] = Field(default_factory=list)
    is_fixture: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


# ================================================================ ObjectType list

OBJECT_TYPES = [
    ObjectType(
        name="aggregator_profile",
        schema=AggregatorProfile,
        description=(
            "A thin route-provider profile: auth state and available/enabled services only."
        ),
    ),
    ObjectType(
        name="integration_profile",
        schema=IntegrationProfile,
        description=(
            "Versioned, evidence-backed understanding of one canonical service/account."
        ),
    ),
    ObjectType(
        name="integration_exploration",
        schema=IntegrationExploration,
        description="A budgeted R0 exploration receipt with a shape fingerprint.",
    ),
    ObjectType(
        name="tool_policy",
        schema=ToolPolicy,
        description=(
            "A standing-scope tool policy: a versioned, provenance-carrying "
            "governed artifact earned from sustained prediction accuracy "
            "(ADR 0018). Promoted policies let the action-class dimension "
            "auto-approve one bounded reversible capability."
        ),
    ),
    ObjectType(
        name="capability_provider",
        schema=CapabilityProvider,
        description=(
            "A registered external capability provider (API, local function, MCP, SDK). "
            "Registered once, referenced by ID in CapabilityCall objects."
        ),
    ),
    ObjectType(
        name="capability_call",
        schema=CapabilityCall,
        description=(
            "A proposed or executing capability call. All external calls must flow "
            "through here for policy checks, credential injection, and recording."
        ),
    ),
    ObjectType(
        name="capability_approval",
        schema=CapabilityApproval,
        description=(
            "Records that a capability_call was approved for execution. "
            "Serves as the graph-visible trigger for call_executor behavior."
        ),
    ),
    ObjectType(
        name="capability_denial",
        schema=CapabilityDenial,
        description=(
            "Records that a held capability_call was explicitly denied, by whom, "
            "and why — refusals are first-class audit objects, not status flips."
        ),
    ),
    ObjectType(
        name="capability_result",
        schema=CapabilityResult,
        description=(
            "The result of an executed capability call. Mapped to a Core source "
            "object so downstream behaviors can observe the output."
        ),
    ),
    ObjectType(
        name="injection_flag",
        schema=InjectionFlag,
        description=(
            "Audit record that a capability result matched prompt-injection "
            "patterns. Never blocks execution — makes the attempt visible."
        ),
    ),
]


# ================================================================ RelationType list

RELATION_TYPES = [
    RelationType(
        name="integration_supersedes",
        source_types=("integration_profile",),
        target_types=("integration_profile",),
        description="A refreshed integration profile supersedes its prior version.",
    ),
    RelationType(
        name="profile_explored_by",
        source_types=("integration_profile",),
        target_types=("integration_exploration",),
        description="An integration profile was supported by a recorded exploration receipt.",
    ),
    RelationType(
        name="calls",
        source_types=("capability_call",),
        target_types=("capability_provider",),
        description="A capability call invokes a capability provider.",
    ),
    RelationType(
        name="approved_by",
        source_types=("capability_call",),
        target_types=("capability_approval",),
        description="A capability call is approved by a capability_approval record.",
    ),
    RelationType(
        name="denied_by",
        source_types=("capability_call",),
        target_types=("capability_denial",),
        description="A capability call was denied by a capability_denial record.",
    ),
    RelationType(
        name="produces_result",
        source_types=("capability_call",),
        target_types=("capability_result",),
        description="An executed capability call produces a result.",
    ),
    RelationType(
        name="sourced_as",
        source_types=("capability_result",),
        target_types=("source",),
        description=(
            "A capability result is sourced as a Core source object, "
            "enabling downstream observation extraction."
        ),
    ),
    RelationType(
        name="flags",
        source_types=("injection_flag",),
        target_types=("capability_result",),
        description="An injection_flag marks a capability_result as suspect.",
    ),
]
