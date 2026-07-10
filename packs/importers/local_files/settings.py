"""Settings for the bounded Local Files snapshot importer."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LocalFilesSettings(BaseModel):
    """Zero-key defaults for deterministic directory snapshots."""

    artifact_store_dir: str = Field(
        default=".activegraph/replay-artifacts",
        description="Root of the local content-addressed replay artifact store.",
    )
    replay_mode: str = Field(
        default="artifact",
        description="Default replay policy: artifact, inline, or reference_only.",
    )
    max_files: int = Field(
        default=1000,
        ge=1,
        le=100_000,
        description="Maximum supported files in one bounded snapshot.",
    )
    max_file_bytes: int = Field(
        default=2_000_000,
        ge=1,
        description="Maximum bytes read from any one source file.",
    )
    max_normalized_chars: int = Field(
        default=8192,
        ge=1,
        description="Maximum derived reasoning characters retained in the graph.",
    )
    extensions: list[str] = Field(
        default_factory=lambda: [".txt", ".md", ".markdown", ".json"],
        description="Case-insensitive file extensions included in a snapshot.",
    )
