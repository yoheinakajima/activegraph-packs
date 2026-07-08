"""Tests for managed credentials (packs/secrets/managed.py).

The contract: managed sources sit BEHIND resolve_credential_fn, env
always wins, the OAuth device flow works end to end against a fake
provider (no network), tokens refresh on expiry, values never enter the
graph, and the existing audit trail (SecretUsageEvent) names the source.
All deterministic: injected HTTP, injected clock.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import pytest

from activegraph import Graph, Runtime

from packs.core import pack as core_pack
from packs.secrets import pack as secrets_pack
from packs.secrets.managed import (
    OAuthCredentialSource,
    OAuthDeviceFlow,
    OAuthTokenStore,
    clear_credential_sources,
    register_credential_source,
)
from packs.secrets.tools import (
    resolve_and_audit_fn,
    resolve_credential_fn,
    resolve_credential_with_source_fn,
)

SECRET = "managed-access-token-abc123"
REFRESHED = "refreshed-access-token-def456"


class FakeProvider:
    """A deterministic OAuth provider: device flow + refresh, no network."""

    def __init__(self):
        self.approved = False
        self.refresh_ok = True
        self.calls: list[tuple[str, dict]] = []

    def http_post(self, url: str, data: dict, timeout: float = 15.0):
        self.calls.append((url, dict(data)))
        if url.endswith("/device"):
            return 200, {"device_code": "dev-1", "user_code": "ABCD-1234",
                         "verification_uri": "https://fake/activate",
                         "interval": 1, "expires_in": 600}
        if url.endswith("/token"):
            grant = data.get("grant_type", "")
            if grant.endswith("device_code"):
                if not self.approved:
                    return 400, {"error": "authorization_pending"}
                return 200, {"access_token": SECRET, "refresh_token": "ref-1",
                             "expires_in": 3600, "token_type": "Bearer"}
            if grant == "refresh_token":
                if not self.refresh_ok:
                    return 400, {"error": "invalid_grant"}
                return 200, {"access_token": REFRESHED, "expires_in": 3600}
        return 404, {}


def _flow(provider: FakeProvider) -> OAuthDeviceFlow:
    return OAuthDeviceFlow(
        provider="fake",
        client_id="client-1",
        device_authorization_endpoint="https://fake/device",
        token_endpoint="https://fake/token",
        scope="read",
        http_post=provider.http_post,
    )


@pytest.fixture(autouse=True)
def _clean_chain():
    clear_credential_sources()
    yield
    clear_credential_sources()


# ---------------------------------------------------------------- device flow


def test_device_flow_start_poll_lifecycle():
    provider = FakeProvider()
    flow = _flow(provider)

    started = flow.start()
    assert started["user_code"] == "ABCD-1234"
    assert started["verification_uri"] == "https://fake/activate"

    assert flow.poll("dev-1")["status"] == "pending"
    provider.approved = True
    outcome = flow.poll("dev-1")
    assert outcome["status"] == "ok"
    assert outcome["token"]["access_token"] == SECRET


def test_device_flow_terminal_error():
    provider = FakeProvider()

    def denying_post(url, data, timeout=15.0):
        if url.endswith("/token"):
            return 400, {"error": "access_denied"}
        return provider.http_post(url, data)

    flow = _flow(provider)
    flow.http_post = denying_post
    assert flow.poll("dev-1") == {"status": "error", "error": "access_denied"}


# ---------------------------------------------------------------- store + source


def test_store_roundtrip_and_names_never_values():
    store = OAuthTokenStore(":memory:")
    store.put("GITHUB_TOKEN", access_token=SECRET, refresh_token="ref-1",
              expires_in=3600, now=1000.0)
    record = store.get("GITHUB_TOKEN")
    assert record["access_token"] == SECRET
    assert record["expires_at"] == 4600.0
    assert store.names() == ["GITHUB_TOKEN"]  # names are displayable, values stay put
    store.delete("GITHUB_TOKEN")
    assert store.get("GITHUB_TOKEN") is None


def test_source_returns_live_token_and_refreshes_expired():
    provider = FakeProvider()
    clock = {"now": 1000.0}
    store = OAuthTokenStore(":memory:")
    source = OAuthCredentialSource(store, now_fn=lambda: clock["now"])
    store.put("GITHUB_TOKEN", access_token=SECRET, refresh_token="ref-1",
              expires_in=3600, now=clock["now"])
    source.add_flow("GITHUB_TOKEN", _flow(provider))

    # Live token: returned as stored.
    assert source.resolve("GITHUB_TOKEN") == SECRET

    # Past expiry: refresh runs, new token returned AND persisted.
    clock["now"] = 5000.0
    assert source.resolve("GITHUB_TOKEN") == REFRESHED
    assert store.get("GITHUB_TOKEN")["access_token"] == REFRESHED
    # The old refresh token is kept when the provider omits a new one.
    assert store.get("GITHUB_TOKEN")["refresh_token"] == "ref-1"


def test_expired_without_refresh_path_fails_closed():
    clock = {"now": 1000.0}
    store = OAuthTokenStore(":memory:")
    source = OAuthCredentialSource(store, now_fn=lambda: clock["now"])
    store.put("NO_REFRESH", access_token=SECRET, expires_in=60, now=clock["now"])

    clock["now"] = 2000.0
    assert source.resolve("NO_REFRESH") is None


def test_dead_grant_fails_closed():
    provider = FakeProvider()
    provider.refresh_ok = False
    clock = {"now": 1000.0}
    store = OAuthTokenStore(":memory:")
    source = OAuthCredentialSource(store, now_fn=lambda: clock["now"])
    store.put("GH", access_token=SECRET, refresh_token="ref-1",
              expires_in=60, now=clock["now"])
    source.add_flow("GH", _flow(provider))

    clock["now"] = 2000.0
    assert source.resolve("GH") is None


# ---------------------------------------------------------------- the seam


def test_resolution_chain_env_wins(monkeypatch):
    store = OAuthTokenStore(":memory:")
    store.put("MY_API_KEY", access_token="from-store")
    register_credential_source(OAuthCredentialSource(store))

    monkeypatch.setenv("MY_API_KEY", "from-env")
    value, source = resolve_credential_with_source_fn("MY_API_KEY")
    assert (value, source) == ("from-env", "env")

    monkeypatch.delenv("MY_API_KEY")
    value, source = resolve_credential_with_source_fn("MY_API_KEY")
    assert (value, source) == ("from-store", "oauth_token_store")

    assert resolve_credential_fn("UNKNOWN_KEY") is None


def test_broken_source_does_not_block_chain():
    class ExplodingSource:
        label = "exploding"

        def resolve(self, name):
            raise RuntimeError("down")

    store = OAuthTokenStore(":memory:")
    store.put("K", access_token="v")
    register_credential_source(ExplodingSource())
    register_credential_source(OAuthCredentialSource(store))
    assert resolve_credential_fn("K") == "v"


# ---------------------------------------------------------------- audit + graph hygiene


def test_audit_names_source_and_value_never_enters_graph():
    store = OAuthTokenStore(":memory:")
    store.put("MANAGED_KEY", access_token=SECRET)
    register_credential_source(OAuthCredentialSource(store))

    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    rt.load_pack(secrets_pack)

    value = resolve_and_audit_fn(rt.graph, "MANAGED_KEY",
                                 behavior_name="test_behavior")
    assert value == SECRET
    rt.run_until_idle()

    (usage,) = list(rt.graph.objects(type="secret_usage_event"))
    assert usage.data["resolved"] is True
    assert usage.data["metadata"]["source"] == "oauth_token_store"
    assert usage.data["metadata"]["found_in_env"] is False

    # The invariant that matters: the secret VALUE appears nowhere in the
    # graph, in any object of any type.
    for obj in rt.graph.all_objects():
        assert SECRET not in str(obj.data), f"secret leaked into {obj.type}"
