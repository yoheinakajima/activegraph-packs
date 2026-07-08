"""Managed credentials behind the resolve_credential_fn seam — v0.2.

Until now every credential was an environment variable. This module adds
managed sources behind the SAME resolution seam the gateway already
uses, so nothing downstream changes: `resolve_credential_fn` consults
the environment first (existing behavior, always wins), then any
registered `CredentialSource`.

The first managed source is OAuth 2.0 Device Authorization Grant
(RFC 8628) — the "go to this URL, enter this code" flow that fits a
headless assistant: no redirect URI, no local browser, works from a
server. Components:

  * ``OAuthTokenStore``  — SQLite persistence for tokens. Tokens live in
    this store and ONLY this store: never in the graph, never in events,
    never in logs (the Secrets Pack invariant, unchanged).
  * ``OAuthDeviceFlow``  — one provider's endpoints + client id. HTTP is
    an injectable callable, so tests run the whole flow deterministically
    with a fake; the default uses stdlib urllib.
  * ``OAuthCredentialSource`` — the resolver: looks up the token for a
    credential name, refreshes through the provider when expired (when a
    refresh token exists), persists the refresh, returns the access
    token.

Auditing is unchanged and automatic: resolution still flows through
resolve_and_audit_fn, which records a SecretUsageEvent naming the
credential and (now) the source that satisfied it. The value itself
never touches the graph.
"""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, runtime_checkable

# ---------------------------------------------------------------- source seam


@runtime_checkable
class CredentialSource(Protocol):
    """Anything that can resolve a credential name to a secret value.

    ``resolve`` returns the value or None; it must not raise for a merely
    unknown name. ``label`` identifies the source in audit metadata."""

    def resolve(self, credential_name: str) -> Optional[str]: ...

    @property
    def label(self) -> str: ...


_sources: list[CredentialSource] = []


def register_credential_source(source: CredentialSource) -> None:
    """Add a managed source to the resolution chain (consulted after env,
    in registration order)."""
    _sources.append(source)


def registered_credential_sources() -> list[CredentialSource]:
    return list(_sources)


def clear_credential_sources() -> None:
    """Reset the chain (tests)."""
    _sources.clear()


# ---------------------------------------------------------------- token store


class OAuthTokenStore:
    """SQLite-backed token persistence, keyed by credential name.

    A separate file from the event log and the memory store, because its
    contents are secrets: the graph stores may be exported, inspected,
    or shipped to an Inspector; this file must never be."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS oauth_tokens (
                credential_name TEXT PRIMARY KEY,
                access_token TEXT NOT NULL,
                refresh_token TEXT,
                token_type TEXT DEFAULT 'Bearer',
                scope TEXT DEFAULT '',
                expires_at REAL,
                obtained_at REAL,
                provider TEXT DEFAULT ''
            )
        """)
        self._conn.commit()

    def put(self, credential_name: str, *, access_token: str,
            refresh_token: Optional[str] = None, expires_in: Optional[float] = None,
            token_type: str = "Bearer", scope: str = "", provider: str = "",
            now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        expires_at = (now + float(expires_in)) if expires_in else None
        self._conn.execute(
            """INSERT OR REPLACE INTO oauth_tokens
               (credential_name, access_token, refresh_token, token_type,
                scope, expires_at, obtained_at, provider)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (credential_name, access_token, refresh_token, token_type,
             scope, expires_at, now, provider),
        )
        self._conn.commit()

    def get(self, credential_name: str) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            """SELECT access_token, refresh_token, token_type, scope,
                      expires_at, obtained_at, provider
               FROM oauth_tokens WHERE credential_name = ?""",
            (credential_name,),
        ).fetchone()
        if row is None:
            return None
        keys = ["access_token", "refresh_token", "token_type", "scope",
                "expires_at", "obtained_at", "provider"]
        return dict(zip(keys, row))

    def delete(self, credential_name: str) -> None:
        self._conn.execute("DELETE FROM oauth_tokens WHERE credential_name = ?",
                           (credential_name,))
        self._conn.commit()

    def names(self) -> list[str]:
        """Stored credential NAMES (safe to display; values stay put)."""
        return [r[0] for r in self._conn.execute(
            "SELECT credential_name FROM oauth_tokens ORDER BY credential_name")]

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------- device flow


def _default_http_post(url: str, data: dict[str, str],
                       timeout: float = 15.0) -> tuple[int, dict]:
    """Form-encoded POST returning (status, parsed JSON). stdlib only."""
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:  # OAuth errors come as 4xx + JSON body
        try:
            return exc.code, json.loads(exc.read().decode() or "{}")
        except Exception:
            return exc.code, {}


@dataclass
class OAuthDeviceFlow:
    """One OAuth provider's device-grant endpoints (RFC 8628).

    ``http_post`` is injectable so the full flow is testable offline."""

    provider: str
    client_id: str
    device_authorization_endpoint: str
    token_endpoint: str
    scope: str = ""
    client_secret: str = ""          # some providers require it on token calls
    http_post: Callable[..., tuple[int, dict]] = field(default=_default_http_post)

    def start(self) -> dict[str, Any]:
        """Begin the flow. Returns the RFC 8628 response: device_code,
        user_code, verification_uri, interval, expires_in. The user_code
        and verification_uri go to the OWNER; the device_code stays
        server-side for polling."""
        data = {"client_id": self.client_id}
        if self.scope:
            data["scope"] = self.scope
        status, payload = self.http_post(self.device_authorization_endpoint, data)
        if status != 200 or "device_code" not in payload:
            raise RuntimeError(
                f"device authorization failed for {self.provider!r}: "
                f"HTTP {status} {payload.get('error', '')}"
            )
        return payload

    def poll(self, device_code: str) -> dict[str, Any]:
        """One poll of the token endpoint. Returns:
        {'status': 'pending'} while the owner hasn't approved,
        {'status': 'ok', 'token': {...}} on success,
        {'status': 'error', 'error': ...} on terminal failure."""
        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": self.client_id,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        status, payload = self.http_post(self.token_endpoint, data)
        if status == 200 and "access_token" in payload:
            return {"status": "ok", "token": payload}
        error = payload.get("error", f"http_{status}")
        if error in ("authorization_pending", "slow_down"):
            return {"status": "pending", "error": error}
        return {"status": "error", "error": error}

    def refresh(self, refresh_token: str) -> Optional[dict[str, Any]]:
        """Exchange a refresh token. Returns the new token payload or None
        (terminal refresh failure: the stored grant is dead)."""
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        status, payload = self.http_post(self.token_endpoint, data)
        if status == 200 and "access_token" in payload:
            return payload
        return None


# ---------------------------------------------------------------- the source


class OAuthCredentialSource:
    """Resolves credential names from the token store, refreshing expired
    tokens through their provider's flow when possible."""

    # Refresh this many seconds before nominal expiry, so a token never
    # gets handed out with only moments to live.
    EXPIRY_MARGIN = 60.0

    def __init__(self, store: OAuthTokenStore,
                 flows: Optional[dict[str, OAuthDeviceFlow]] = None,
                 now_fn: Callable[[], float] = time.time):
        self.store = store
        self.flows = flows or {}
        self._now = now_fn

    @property
    def label(self) -> str:
        return "oauth_token_store"

    def add_flow(self, credential_name: str, flow: OAuthDeviceFlow) -> None:
        self.flows[credential_name] = flow

    def resolve(self, credential_name: str) -> Optional[str]:
        record = self.store.get(credential_name)
        if record is None:
            return None
        expires_at = record.get("expires_at")
        if expires_at is not None and self._now() >= expires_at - self.EXPIRY_MARGIN:
            return self._refresh(credential_name, record)
        return record["access_token"]

    def _refresh(self, credential_name: str, record: dict) -> Optional[str]:
        refresh_token = record.get("refresh_token")
        flow = self.flows.get(credential_name)
        if not refresh_token or flow is None:
            return None  # expired with no refresh path: fail closed
        payload = flow.refresh(refresh_token)
        if payload is None:
            return None  # dead grant stays stored for diagnosis; resolve fails closed
        self.store.put(
            credential_name,
            access_token=payload["access_token"],
            # Providers may rotate or omit the refresh token; keep the old
            # one when omitted (RFC 6749 §6).
            refresh_token=payload.get("refresh_token", refresh_token),
            expires_in=payload.get("expires_in"),
            token_type=payload.get("token_type", record.get("token_type", "Bearer")),
            scope=payload.get("scope", record.get("scope", "")),
            provider=record.get("provider", ""),
            now=self._now(),
        )
        return payload["access_token"]
