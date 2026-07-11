"""Gmail connector policy. Bounds are versioned settings, not architecture."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GmailSettings(BaseModel):
    toolkit_version: str = Field(default="20260703_00")
    artifact_store_dir: str = Field(default=".activegraph/replay-artifacts")
    default_query: str = Field(default="newer_than:30d")
    default_page_size: int = Field(default=25, ge=1, le=100)
    default_max_messages: int = Field(default=250, ge=1, le=5000)
    default_max_pages: int = Field(default=10, ge=1, le=100)
    live_poll_max_messages: int = Field(default=100, ge=1, le=500)
    max_normalized_chars: int = Field(default=32_000, ge=1000, le=200_000)
    max_replay_payload_bytes: int = Field(default=5_000_000, ge=1024)


__all__ = ["GmailSettings"]
