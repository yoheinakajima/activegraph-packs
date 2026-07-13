"""Web research runs: consented, derived, bounded — never ambient (ADR 0040)."""

from __future__ import annotations

from typing import Any, Optional

from activegraph.packs import ObjectType
from pydantic import BaseModel, ConfigDict, Field


class WebResearchRun(BaseModel):
    """One approved-plan research execution with its findings ledger."""

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
    error: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


OBJECT_TYPES = [
    ObjectType(
        "web_research_run",
        WebResearchRun,
        "One consented web-research execution bound to its approved plan.",
    ),
]
RELATION_TYPES = []

__all__ = ["WebResearchRun", "OBJECT_TYPES", "RELATION_TYPES"]
