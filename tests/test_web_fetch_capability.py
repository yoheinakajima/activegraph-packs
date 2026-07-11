"""The shared ``web.fetch_url`` capability against the fail-closed runtime.

Runtime v1.9's reference ``web_fetch`` rejects ctx-less external I/O, so
the gateway capability must pass an explicit live ToolContext (the same
fix public_presence applied in slice 5a). These tests drive the real
``web_fetch`` code path — real ToolContext, real fail-closed guard —
with only the HTTP transport (urllib.request.urlopen) mocked.
"""

from __future__ import annotations

import io
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import pytest

from packs.tool_gateway.capabilities import register_web_fetch_capability
from packs.tool_gateway.tools import clear_local_registry, get_capability_spec


class _FakeResponse(io.BytesIO):
    """Just enough of an http.client response for web_fetch."""

    def __init__(self, body: bytes, status: int, url: str) -> None:
        super().__init__(body)
        self._status = status
        self._url = url

    def getcode(self) -> int:
        return self._status

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


@pytest.fixture()
def mock_transport(monkeypatch):
    """Mock the HTTP leaf only; everything above it runs for real."""
    seen: list[urllib.request.Request] = []

    def _fake_urlopen(req, timeout=None):
        seen.append(req)
        return _FakeResponse(
            b"<html>hello from the fixture page</html>", 200, req.full_url
        )

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    return seen


def test_web_fetch_capability_passes_a_live_tool_context(mock_transport):
    """The registered capability survives v1.9's fail-closed guard.

    Before the fix, ``_fetch`` passed ``None`` as the ToolContext and the
    guard's ``ctx.external_io_mode`` read raised AttributeError the moment
    the capability was exercised live. With an explicit live context the
    call reaches the (mocked) transport and returns the page.
    """
    clear_local_registry()
    try:
        spec = register_web_fetch_capability()
        assert spec.key == "web.fetch_url"

        out = spec.fn(url="https://example.com/page", timeout_seconds=5.0)

        assert out["status"] == 200
        assert out["final_url"] == "https://example.com/page"
        assert "hello from the fixture page" in out["text"]
        (request,) = mock_transport
        assert request.full_url == "https://example.com/page"
    finally:
        clear_local_registry()


def test_runtime_web_fetch_still_fails_closed_without_a_context():
    """The guard the capability must satisfy: no context, no external I/O."""
    from activegraph.tools.context import ToolContext
    from activegraph.tools.errors import ToolError
    from activegraph.tools.web_fetch import WebFetchInput, web_fetch

    forbid_ctx = ToolContext(
        behavior_name="test", event_id="", frame=None,
        idempotency_key="", timeout_seconds=1.0,
    )
    with pytest.raises(ToolError):
        web_fetch.fn(WebFetchInput(url="https://example.com"), forbid_ctx)
