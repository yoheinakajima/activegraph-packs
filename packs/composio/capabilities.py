"""Gateway registrations for the thin Composio route."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .client import composio_transport, connection_summary, store_redirect


class LinkServiceInput(BaseModel):
    user_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    callback_url: str = Field(min_length=1)


class ConnectionStatusInput(BaseModel):
    user_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    limit: int = Field(default=25, ge=1, le=100)


def register_composio_capabilities(*, api_key_env: str = "COMPOSIO_API_KEY") -> tuple:
    from packs.tool_gateway.tools import register_local_capability

    def _link_service(
        user_id: str,
        service: str,
        callback_url: str,
        execution_context: Optional[dict] = None,
    ) -> dict:
        response = composio_transport(api_key_env=api_key_env).authorize(
            user_id=user_id, toolkit=service, callback_url=callback_url
        )
        safe = store_redirect(str(response.get("id") or ""), str(response.get("redirect_url") or ""))
        return {"ok": True, "service": service, "status": response.get("status"), **safe}

    def _connection_status(
        user_id: str,
        service: str,
        limit: int = 25,
        execution_context: Optional[dict] = None,
    ) -> dict:
        rows = composio_transport(api_key_env=api_key_env).list_connections(
            user_id=user_id, toolkit=service, limit=limit
        )
        return {
            "ok": True,
            "service": service,
            "connections": [connection_summary(row) for row in rows],
        }

    return (
        register_local_capability(
            "composio", "connections.link", _link_service,
            input_schema=LinkServiceInput,
            description="Create a hosted Composio Connect Link for one explicitly selected service.",
            risk_class="high", action_class="R4",
        ),
        register_local_capability(
            "composio", "connections.status", _connection_status,
            input_schema=ConnectionStatusInput,
            description="Read connection status for one selected Composio service.",
            risk_class="low", action_class="R0",
        ),
    )


__all__ = ["LinkServiceInput", "ConnectionStatusInput", "register_composio_capabilities"]
