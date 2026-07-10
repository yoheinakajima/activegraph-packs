"""Bounds and replay defaults for official ChatGPT export snapshots."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatGPTExportSettings(BaseModel):
    artifact_store_dir: str = Field(
        default=".activegraph/replay-artifacts",
        description="Root of the shared content-addressed replay artifact store.",
    )
    replay_mode: str = Field(default="artifact")
    max_archive_bytes: int = Field(default=1_000_000_000, ge=1)
    max_conversations_json_bytes: int = Field(default=256_000_000, ge=1)
    max_compression_ratio: int = Field(default=200, ge=1, le=10_000)
    max_conversations: int = Field(default=10_000, ge=1, le=1_000_000)
    max_nodes_per_conversation: int = Field(default=100_000, ge=1, le=1_000_000)
    max_messages: int = Field(default=250_000, ge=1, le=5_000_000)
    max_normalized_chars: int = Field(default=32_000, ge=1, le=1_000_000)


__all__ = ["ChatGPTExportSettings"]
