"""Tests for the capability catalog (tool_gateway/catalog.py) and its
inbound MCP surface.

The catalog's contract: every registered capability queryable with
governance metadata (risk class, origin, LLM-exposability, allow-list
status), the agent searches it through a governed call, and the inbound
MCP view is scoped to what the caller's role can actually reach.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import pytest
from pydantic import BaseModel, Field

from activegraph import Graph, Runtime
from activegraph.tools.context import ToolContext

from packs.core import pack as core_pack
from packs.mcp import pack as mcp_pack, MCPSettings
from packs.mcp.client import MCPClient
from packs.mcp.registry import connect_and_register
from packs.mcp.server import MCPGateway, ensure_default_exposures, set_exposure_fn
from packs.tool_gateway import pack as tg_pack, ToolGatewaySettings
from packs.tool_gateway.catalog import catalog_entries, register_catalog_capability
from packs.tool_gateway.llm_tools import llm_tools_for
from packs.tool_gateway.tools import (
    clear_local_registry,
    get_capability_spec,
    register_local_capability,
)

from test_mcp_client import FakeTransport


class EchoInput(BaseModel):
    text: str = Field(default="", description="Text to echo.")


def _ctx() -> ToolContext:
    return ToolContext(behavior_name="test_behavior", event_id="evt_test",
                       frame=None, idempotency_key="k", timeout_seconds=30.0)


@pytest.fixture()
def populated_registry():
    """A registry with native + MCP-derived + never-callable capabilities."""
    clear_local_registry()
    register_local_capability(
        "web", "fetch_url", lambda url="": {"text": "ok"},
        input_schema=EchoInput, description="Fetch a page.", risk_class="low",
    )
    register_local_capability(
        "mail", "send", lambda text="": {"sent": text},
        input_schema=EchoInput, description="Send mail.", risk_class="high",
    )
    register_local_capability(
        "shadow", "approve_capability", lambda: {"ok": True},
        input_schema=EchoInput, description="Disguised approval.", risk_class="low",
    )
    connect_and_register("fake", MCPClient(FakeTransport()),
                         tool_risk_overrides={"search": "low"})
    yield
    clear_local_registry()


# ---------------------------------------------------------------- entries


def test_catalog_shape_and_origin(populated_registry):
    entries = catalog_entries()
    by_key = {e["key"]: e for e in entries}

    assert by_key["web.fetch_url"]["origin"] == "native"
    assert by_key["mcp_fake.search"]["origin"] == "mcp:fake"
    assert by_key["mcp_fake.search"]["risk_class"] == "low"
    assert by_key["mcp_fake.delete_everything"]["risk_class"] == "high"
    # Deterministic ordering by key.
    assert [e["key"] for e in entries] == sorted(e["key"] for e in entries)


def test_never_llm_callable_is_flagged_not_hidden(populated_registry):
    """The disguised approval capability appears in the catalog (humans
    must see it) but is marked never-exposable."""
    (entry,) = [e for e in catalog_entries() if e["capability"] == "approve_capability"]
    assert entry["never_llm_callable"] is True
    assert entry["llm_exposable"] is False


def test_catalog_filters(populated_registry):
    assert {e["key"] for e in catalog_entries(origin="mcp")} == {
        "mcp_fake.search", "mcp_fake.delete_everything"}
    assert {e["key"] for e in catalog_entries(risk_class="high")} == {
        "mcp_fake.delete_everything", "mail.send"}
    assert {e["key"] for e in catalog_entries(query="page")} == {"web.fetch_url"}


def test_allow_list_annotation(populated_registry):
    allow = ["web.fetch_url", "mcp_fake.search"]
    by_key = {e["key"]: e for e in catalog_entries(allow_list=allow)}
    assert by_key["web.fetch_url"]["allowed_now"] is True
    assert by_key["mail.send"]["allowed_now"] is False
    # Without an allow-list the annotation is null, never a guess.
    assert catalog_entries()[0]["allowed_now"] is None


# ------------------------------------------------------- governed search


def test_catalog_search_runs_through_gateway(populated_registry):
    """The agent's catalog access is itself a recorded capability call."""
    register_catalog_capability(lambda: ["web.fetch_url"])
    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    rt.load_pack(tg_pack, settings=ToolGatewaySettings())

    (tool,) = llm_tools_for(rt.graph, ["catalog.search"])
    model = get_capability_spec("catalog.search").input_schema
    out = tool.fn(model(query="fetch"), _ctx())

    assert out["status"] == "done"
    payload = out["output"]
    assert "web.fetch_url" in payload
    assert '"allowed_now": true' in payload
    (call,) = list(rt.graph.objects(type="capability_call"))
    assert call.data["capability_name"] == "search"
    assert call.data["provider_name"] == "catalog"


# ------------------------------------------------------- inbound MCP view


@pytest.fixture()
def inbound(populated_registry):
    # This fixture exercises unverified-token mode (no identity pack), so
    # the in-process principal registry must be empty: another suite's
    # leftover principals would flip resolve_caller into verified mode.
    from packs.identity_auth.behaviors import clear_principal_registry
    clear_principal_registry()

    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    rt.load_pack(tg_pack, settings=ToolGatewaySettings())
    settings = MCPSettings(
        tokens={"tok-owner": "owner@example.com"},
        expose_capabilities=["web.fetch_url", "mail.send"],
    )
    rt.load_pack(mcp_pack, settings=settings)
    ensure_default_exposures(rt.graph, settings)
    gateway = MCPGateway(
        lambda: rt.graph, settings,
        chat_fn=lambda **k: {"content": "hi", "session_id": "s"},
        memory_fn=lambda **k: [],
    )
    return rt, gateway


def _call(gateway, name, args, token):
    return gateway.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
         "params": {"name": name, "arguments": args}}, token)


def test_inbound_catalog_is_role_scoped(inbound):
    rt, gateway = inbound
    response = _call(gateway, "catalog_search", {}, "tok-owner")
    payload = json.loads(response["result"]["content"][0]["text"])
    keys = {e["key"] for e in payload["capabilities"]}
    # Owner sees their reachable surfaces + exposed tools, with hold info.
    assert keys == {"chat", "memory_search", "web.fetch_url", "mail.send"}
    by_key = {e["key"]: e for e in payload["capabilities"]}
    assert by_key["mail.send"]["held_on_call"] is True     # high risk
    assert by_key["web.fetch_url"]["held_on_call"] is False

    # Narrow one tool's exposure away: it disappears from the catalog too.
    set_exposure_fn(rt.graph, "tool:mail.send", [], enabled=False)
    payload = json.loads(
        _call(gateway, "catalog_search", {}, "tok-owner")["result"]["content"][0]["text"])
    assert "mail.send" not in {e["key"] for e in payload["capabilities"]}


def test_inbound_catalog_respects_its_own_exposure(inbound):
    rt, gateway = inbound
    set_exposure_fn(rt.graph, "catalog", [], enabled=False, note="off")
    response = _call(gateway, "catalog_search", {}, "tok-owner")
    assert "error" in response and response["error"]["code"] == -32003


def test_inbound_catalog_never_reveals_unexposed_registry(inbound):
    """The full registry contains capabilities the inbound caller cannot
    reach (shadow.approve_capability, mcp_fake.*); none may leak."""
    _, gateway = inbound
    payload = json.loads(
        _call(gateway, "catalog_search", {}, "tok-owner")["result"]["content"][0]["text"])
    keys = {e["key"] for e in payload["capabilities"]}
    assert not any(k.startswith("mcp_fake") or k.startswith("shadow") for k in keys)
