"""Optional Composio SDK adapter behind a small, injectable route seam.

Managed OAuth uses the current Connect Link/session authorization flow.  The
legacy ``connected_accounts.initiate`` path is deliberately absent: Composio
retired it for managed OAuth on 2026-07-03.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional, Protocol
from urllib.parse import urlsplit

from packs.tool_gateway.integrations import safe_sha256_fingerprint


class ComposioUnavailable(RuntimeError):
    pass


class ComposioTransport(Protocol):
    def authorize(self, *, user_id: str, toolkit: str, callback_url: str) -> dict[str, Any]: ...
    def list_connections(self, *, user_id: str, toolkit: str, limit: int) -> list[dict[str, Any]]: ...
    def execute(
        self,
        *,
        tool_slug: str,
        arguments: dict[str, Any],
        user_id: str,
        connected_account_id: str,
        version: str,
    ) -> dict[str, Any]: ...


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


class SDKComposioTransport:
    def __init__(self, *, api_key_env: str = "COMPOSIO_API_KEY") -> None:
        api_key = os.environ.get(api_key_env, "").strip()
        if not api_key:
            raise ComposioUnavailable(
                f"{api_key_env} is not set; add a Composio project key and restart the engine"
            )
        try:
            from composio import Composio
        except ImportError as exc:
            raise ComposioUnavailable(
                "Composio SDK is not installed; install activegraph-packs[composio]"
            ) from exc
        self._client = Composio(api_key=api_key)

    def authorize(self, *, user_id: str, toolkit: str, callback_url: str) -> dict[str, Any]:
        session = self._client.create(
            user_id=user_id,
            toolkits=[toolkit],
            manage_connections=False,
        )
        request = session.authorize(toolkit, callback_url=callback_url)
        return {
            "id": str(getattr(request, "id", "")),
            "redirect_url": str(getattr(request, "redirect_url", "")),
            "status": str(getattr(request, "status", "INITIATED")),
        }

    def list_connections(self, *, user_id: str, toolkit: str, limit: int) -> list[dict[str, Any]]:
        response = self._client.connected_accounts.list(
            user_ids=[user_id], toolkit_slugs=[toolkit], limit=limit
        )
        rows = getattr(response, "items", []) or []
        return [_plain(row) for row in rows]

    def execute(
        self,
        *,
        tool_slug: str,
        arguments: dict[str, Any],
        user_id: str,
        connected_account_id: str,
        version: str,
    ) -> dict[str, Any]:
        result = self._client.tools.execute(
            tool_slug,
            arguments=arguments,
            user_id=user_id,
            connected_account_id=connected_account_id,
            version=version,
        )
        return _plain(result)


_transport: Optional[ComposioTransport] = None
_redirects: dict[str, str] = {}


def configure_composio_transport(transport: Optional[ComposioTransport]) -> None:
    global _transport
    _transport = transport
    _redirects.clear()


def composio_transport(*, api_key_env: str = "COMPOSIO_API_KEY") -> ComposioTransport:
    global _transport
    if _transport is None:
        _transport = SDKComposioTransport(api_key_env=api_key_env)
    return _transport


def store_redirect(request_id: str, redirect_url: str) -> dict[str, str]:
    """Keep bearer-like Connect Links out of graph output and event history."""

    if not request_id or not redirect_url:
        raise RuntimeError("Composio returned no connection request id or redirect URL")
    parsed = urlsplit(redirect_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("Composio returned an invalid Connect Link")
    _redirects[request_id] = redirect_url
    return {
        "connection_request_id": request_id,
        "redirect_origin": f"{parsed.scheme}://{parsed.netloc}",
        "redirect_url_fingerprint": safe_sha256_fingerprint(redirect_url),
    }


def take_redirect(request_id: str) -> Optional[str]:
    return _redirects.pop(request_id, None)


def connection_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Return only non-secret account identity/status fields."""

    toolkit = row.get("toolkit") or row.get("app") or {}
    if isinstance(toolkit, dict):
        toolkit_slug = toolkit.get("slug") or toolkit.get("name")
    else:
        toolkit_slug = toolkit
    return {
        "id": str(row.get("id") or row.get("connected_account_id") or ""),
        "status": str(row.get("status") or "UNKNOWN").upper(),
        "toolkit": str(toolkit_slug or row.get("toolkit_slug") or ""),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


__all__ = [
    "ComposioUnavailable",
    "ComposioTransport",
    "SDKComposioTransport",
    "configure_composio_transport",
    "composio_transport",
    "store_redirect",
    "take_redirect",
    "connection_summary",
]
