"""Optional Composio SDK adapter behind a small, injectable route seam.

Managed OAuth uses the current Connect Link/session authorization flow.  The
legacy ``connected_accounts.initiate`` path is deliberately absent: Composio
retired it for managed OAuth on 2026-07-03.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, Sequence
from urllib.parse import urlsplit

from packs.tool_gateway.integrations import safe_sha256_fingerprint, shape_fingerprint


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
    def resolve_tool(
        self,
        *,
        toolkit: str,
        candidates: Sequence[str],
        requested_version: str,
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


def resolve_catalog_tool(
    rows: Sequence[Any],
    *,
    toolkit: str,
    candidates: Sequence[str],
    requested_version: str = "latest",
) -> dict[str, Any]:
    """Harden canonical candidates into one concrete provider tool version.

    Discovery is deliberately bounded to caller-supplied candidates.  The
    Composio catalog is route metadata, not a capability namespace we mirror
    into the graph.  A concrete version and schema fingerprint are returned so
    the execution receipt says exactly what was selected.
    """

    ordered = tuple(dict.fromkeys(str(row).strip() for row in candidates if str(row).strip()))
    if not ordered:
        raise ComposioUnavailable(f"no provider tool candidates configured for {toolkit}")
    by_slug: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = _plain(raw)
        if not isinstance(row, dict):
            continue
        slug = str(row.get("slug") or row.get("name") or "")
        row_toolkit = row.get("toolkit") or {}
        if isinstance(row_toolkit, dict):
            row_toolkit = row_toolkit.get("slug") or row_toolkit.get("name")
        if slug and (not row_toolkit or str(row_toolkit).lower() == toolkit.lower()):
            by_slug[slug] = row
    selected = next((by_slug[slug] for slug in ordered if slug in by_slug), None)
    if selected is None:
        raise ComposioUnavailable(
            f"Composio {toolkit} catalog contains none of the configured tools: "
            + ", ".join(ordered)
        )
    slug = str(selected.get("slug") or selected.get("name"))
    current = str(selected.get("version") or "").strip()
    available = [str(row) for row in (selected.get("available_versions") or []) if str(row)]
    if current and current not in available:
        available.insert(0, current)
    requested = str(requested_version or "latest").strip()
    if requested in {"", "latest"}:
        if not current:
            raise ComposioUnavailable(f"Composio returned no concrete version for {slug}")
        resolved_version = current
    elif requested in available:
        resolved_version = requested
    else:
        choices = ", ".join(available) or "none"
        raise ComposioUnavailable(
            f"configured Composio version {requested!r} is unavailable for {slug}; "
            f"available versions: {choices}"
        )
    deprecated = bool(
        selected.get("is_deprecated")
        or (
            isinstance(selected.get("deprecated"), dict)
            and selected["deprecated"].get("is_deprecated")
        )
    )
    if deprecated:
        raise ComposioUnavailable(f"configured Composio tool {slug} is deprecated")
    return {
        "tool_slug": slug,
        "version": resolved_version,
        "input_schema_fingerprint": shape_fingerprint(selected.get("input_parameters") or {}),
        "resolution": "catalog",
    }


class SDKComposioTransport:
    def __init__(self, *, api_key_env: str = "COMPOSIO_API_KEY") -> None:
        from packs.secrets.tools import resolve_credential_fn

        api_key = (resolve_credential_fn(api_key_env) or "").strip()
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
        self._resolved_tools: dict[tuple[str, tuple[str, ...], str], dict[str, Any]] = {}

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

    def resolve_tool(
        self,
        *,
        toolkit: str,
        candidates: Sequence[str],
        requested_version: str,
    ) -> dict[str, Any]:
        ordered = tuple(dict.fromkeys(str(row).strip() for row in candidates if str(row).strip()))
        key = (toolkit.lower(), ordered, str(requested_version or "latest"))
        cached = self._resolved_tools.get(key)
        if cached is not None:
            return dict(cached)
        rows = self._client.tools.get_raw_composio_tools(tools=list(ordered))
        resolved = resolve_catalog_tool(
            rows,
            toolkit=toolkit,
            candidates=ordered,
            requested_version=requested_version,
        )
        self._resolved_tools[key] = dict(resolved)
        return dict(resolved)


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


def peek_redirect(request_id: str) -> bool:
    """Non-consuming claimability check for the one-shot redirect side
    channel: read-only status polling must never consume (or extend) the
    bearer-like link (ADR 0051 §6)."""
    return request_id in _redirects


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
    "resolve_catalog_tool",
    "configure_composio_transport",
    "composio_transport",
    "store_redirect",
    "take_redirect",
    "peek_redirect",
    "connection_summary",
]
