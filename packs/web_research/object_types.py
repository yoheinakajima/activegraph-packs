"""Web research runs: consented, derived, bounded — never ambient (ADR 0040/0045)."""

from __future__ import annotations

from typing import Any, Literal, Optional

from activegraph.packs import ObjectType
from pydantic import BaseModel, ConfigDict, Field


class WebResearchRun(BaseModel):
    """One approved-plan research campaign with its findings ledger."""

    model_config = ConfigDict(extra="forbid")

    run_identity: str = Field(min_length=1)
    source_surface_id: str = Field(min_length=1)
    plan_identity: str = Field(min_length=1)
    queries: list[str] = Field(default_factory=list)
    status: str = Field(default="running", pattern="^(running|completed|partial|failed)$")
    findings: list[dict[str, Any]] = Field(default_factory=list)
    urls_planned: list[str] = Field(default_factory=list)
    urls_ingested: int = Field(default=0, ge=0)
    model: Optional[str] = None
    calls: int = Field(default=0, ge=0)
    rounds_executed: int = Field(default=0, ge=0)
    stop_reason: Optional[str] = None
    error: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchQuery(BaseModel):
    """One frontier entry (ADR 0045 §2): recorded before execution, with
    lineage back to the findings that motivated it and the confirmed owner
    scope it belongs to. Seed queries are pre-registered as plan surfaces;
    their frontier rows are the execution record."""

    model_config = ConfigDict(extra="forbid")

    query_identity: str = Field(min_length=1)
    plan_identity: str = Field(min_length=1)
    run_ref: Optional[str] = None
    text: str = Field(min_length=1)
    origin: Literal["seed", "follow_up"] = "seed"
    parent_query_id: Optional[str] = None
    motivated_by: list[str] = Field(default_factory=list)
    scope_entity: str = ""
    round: int = Field(default=1, ge=1)
    expected_gain: str = ""
    status: Literal[
        "approved_auto",
        "needs_approval",
        "executed",
        "no_results",
        "failed",
        "blocked_scope",
        "declined",
        "skipped_budget",
    ] = "approved_auto"
    result: dict[str, Any] = Field(default_factory=dict)
    injection_flags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchScopeAmendment(BaseModel):
    """A follow-up query that would widen scope, paused for owner review
    (ADR 0045 §2): research never silently broadens outward."""

    model_config = ConfigDict(extra="forbid")

    amendment_identity: str = Field(min_length=1)
    plan_identity: str = Field(min_length=1)
    query_id: str = Field(min_length=1)
    query_text: str = Field(min_length=1)
    reason_kind: Literal["new_entity", "sensitive_topic", "excluded_term"]
    reason_detail: str = ""
    status: Literal["proposed", "approved", "declined"] = "proposed"
    decided_by: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


OBJECT_TYPES = [
    ObjectType(
        "web_research_run",
        WebResearchRun,
        "One consented web-research campaign bound to its approved plan.",
    ),
    ObjectType(
        "research_query",
        ResearchQuery,
        "One recorded frontier query with lineage, scope, and status.",
    ),
    ObjectType(
        "research_scope_amendment",
        ResearchScopeAmendment,
        "A scope-expanding follow-up query paused for owner review.",
    ),
]
RELATION_TYPES = []

__all__ = [
    "WebResearchRun",
    "ResearchQuery",
    "ResearchScopeAmendment",
    "OBJECT_TYPES",
    "RELATION_TYPES",
]
