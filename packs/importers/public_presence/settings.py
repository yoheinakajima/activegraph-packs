"""Budget, bounds, and the fetch-capability seam for public presence."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PublicPresenceSettings(BaseModel):
    artifact_store_dir: str = Field(
        default=".activegraph/replay-artifacts",
        description="Root of the shared content-addressed replay artifact store.",
    )
    max_fetches_per_run: int = Field(
        default=10, ge=1, le=50,
        description="Hard per-bootstrap-run fetch budget; overflow is logged.",
    )
    max_page_chars: int = Field(default=32_000, ge=1_000, le=1_000_000)
    timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    fetch_provider: str = Field(
        default="public_presence",
        description=(
            "Gateway provider for fetches. A keyed Firecrawl-grade upgrade "
            "registers its own capability and is selected HERE — same seam, "
            "suggested, never required."
        ),
    )
    fetch_capability: str = Field(default="fetch_page")


__all__ = ["PublicPresenceSettings"]
