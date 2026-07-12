"""Safety bounds for neutral connector control-plane records."""

from pydantic import BaseModel, Field


class ConnectorControlSettings(BaseModel):
    max_refs_per_delta: int = Field(default=500, ge=1, le=5_000)
    max_exceptions_per_delta: int = Field(default=50, ge=0, le=500)


__all__ = ["ConnectorControlSettings"]
