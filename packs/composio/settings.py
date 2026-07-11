"""Composio route settings. The SDK and key are optional by doctrine."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ComposioSettings(BaseModel):
    api_key_env: str = Field(default="COMPOSIO_API_KEY")
    callback_url: str = Field(default="http://127.0.0.1:8000/composio/callback")
    max_status_accounts: int = Field(default=25, ge=1, le=100)


__all__ = ["ComposioSettings"]
