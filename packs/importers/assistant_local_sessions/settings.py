"""Bounds, window defaults, and default roots for local agent-session logs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AssistantLocalSessionsSettings(BaseModel):
    """Zero-key defaults for bounded local session-log windows.

    Default provider roots live here — never in the importer.  Callers expand
    ``~`` and pass an absolute ``root_path`` to the tool.
    """

    claude_code_root_path: str = Field(
        default="~/.claude/projects",
        description="Default Claude Code projects/ root (caller expands ~).",
    )
    codex_root_path: str = Field(
        default="~/.codex/sessions",
        description="Default Codex sessions/ root (caller expands ~).",
    )
    artifact_store_dir: str = Field(
        default=".activegraph/replay-artifacts",
        description="Root of the shared content-addressed replay artifact store.",
    )
    replay_mode: str = Field(
        default="artifact",
        description="Default replay policy: artifact, inline, or reference_only.",
    )
    max_sessions: int = Field(
        default=20,
        ge=1,
        le=10_000,
        description="Bounded window: most recent session files imported per run.",
    )
    max_file_bytes: int = Field(
        default=50_000_000,
        ge=1,
        description="Maximum bytes read from any one session JSONL file.",
    )
    max_lines_per_file: int = Field(
        default=100_000,
        ge=1,
        le=10_000_000,
        description="Maximum JSONL lines considered per session file.",
    )
    max_messages: int = Field(
        default=25_000,
        ge=1,
        le=5_000_000,
        description="Maximum message units emitted in one run.",
    )
    max_normalized_chars: int = Field(
        default=16_000,
        ge=1,
        le=1_000_000,
        description="Maximum normalized characters retained per message.",
    )


__all__ = ["AssistantLocalSessionsSettings"]
