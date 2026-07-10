"""The bootstrap-run ledger: every fetch planned, executed, or refused."""

from __future__ import annotations

from typing import Any, Literal, Optional

from activegraph.packs import ObjectType, RelationType
from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PresenceBootstrapRun(_StrictModel):
    """One budgeted public-presence bootstrap over a set of handles.

    The budget is hard and the log is first-class: planned URLs, the
    calls actually proposed, and everything skipped (with reasons) are
    all here, so "how many fetches and why" is queryable, not vibes.
    """

    run_identity: str = Field(min_length=1)
    source_surface_id: str = Field(min_length=1)
    handles: dict[str, Any]
    planned_urls: list[str]
    budget: int = Field(ge=1)
    call_ids: list[str] = Field(default_factory=list)
    skipped: list[dict[str, str]] = Field(default_factory=list)
    status: Literal["proposed", "completed"] = "proposed"
    is_fixture: bool = False
    requested_by: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


OBJECT_TYPES = [
    ObjectType(
        "presence_bootstrap_run",
        PresenceBootstrapRun,
        "One budgeted, fully logged public-presence bootstrap run.",
    ),
]

RELATION_TYPES = [
    RelationType(
        "bootstrap_call",
        source_types=("presence_bootstrap_run",),
        target_types=("capability_call",),
        description="A bootstrap run proposed this gateway fetch call.",
    ),
]

__all__ = ["PresenceBootstrapRun", "OBJECT_TYPES", "RELATION_TYPES"]
