"""Tests for the inbound MCP server (packs/mcp/server.py).

The assistant as an MCP server: bearer-token auth resolving to Identity/Auth
principals, graph-native fail-closed exposure rules, chat/memory/capability
dispatch, the governed set_exposure edit path, and the mcp_access audit
trail. All deterministic — fakes stand in for the chat pipeline and memory
backend; the graph and gateway machinery are real.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import pytest
from pydantic import BaseModel, Field

from activegraph import Graph, Runtime

from packs.core import pack as core_pack
from packs.identity_auth import pack as identity_pack, IdentitySettings
from packs.identity_auth.behaviors import clear_principal_registry
from packs.identity_auth.tools import register_principal_fn
from packs.mcp import pack as mcp_pack, MCPSettings
from packs.mcp.server import (
    MCPGateway,
    ensure_default_exposures,
    exposure_allows,
    register_set_exposure_capability,
    resolve_caller,
    set_exposure_fn,
)
from packs.tool_gateway import pack as tg_pack, ToolGatewaySettings
from packs.tool_gateway.tools import (
    approve_capability_fn,
    clear_local_registry,
    register_local_capability,
)

OWNER = "owner@example.com"
AGENT = "agent:researcher"
TOKENS = {"tok-owner": OWNER, "tok-agent": AGENT}


class EchoInput(BaseModel):
    text: str = Field(default="", description="Text to echo.")


def _rpc(method: str, params: dict | None = None, msg_id: int = 1) -> dict:
    message: dict = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _tool_call(name: str, arguments: dict, msg_id: int = 2) -> dict:
    return _rpc("tools/call", {"name": name, "arguments": arguments}, msg_id)


@pytest.fixture()
def env():
    """Runtime with identity (owner + agent principals), gateway, mcp pack,
    seeded default exposures, and an MCPGateway wired to fakes."""
    clear_local_registry()
    clear_principal_registry()

    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    rt.load_pack(tg_pack, settings=ToolGatewaySettings())
    rt.load_pack(identity_pack, settings=IdentitySettings())
    settings = MCPSettings(tokens=TOKENS, expose_capabilities=["util.echo"])
    rt.load_pack(mcp_pack, settings=settings)

    register_principal_fn(rt.graph, OWNER, "owner", name="Owner")
    register_principal_fn(rt.graph, AGENT, "collaborator", name="Researcher")

    register_local_capability(
        "util", "echo", lambda text="": {"echo": text},
        input_schema=EchoInput, description="Echo.", risk_class="low",
    )
    register_set_exposure_capability()
    ensure_default_exposures(rt.graph, settings)

    chat_calls = []

    def chat_fn(message, user_ref, session_id=None):
        chat_calls.append({"message": message, "user_ref": user_ref})
        return {"content": f"reply to {user_ref}: {message}", "session_id": "s1"}

    def memory_fn(query, subject_ref, top_k=5):
        return [{"item_id": "m1", "text": f"memory of {subject_ref} about {query}",
                 "score": 0.9}]

    gateway = MCPGateway(lambda: rt.graph, settings,
                         chat_fn=chat_fn, memory_fn=memory_fn)
    yield rt, gateway, chat_calls
    clear_local_registry()
    clear_principal_registry()


def _text(response: dict) -> str:
    return response["result"]["content"][0]["text"]


# ---------------------------------------------------------------- auth


def test_resolve_caller_matrix(env):
    rt, gateway, _ = env
    settings = gateway.settings
    owner = resolve_caller(rt.graph, "tok-owner", settings)
    assert owner == {"identifier": OWNER, "role": "owner",
                     "verification": "identity_verified"}
    assert resolve_caller(rt.graph, "tok-bogus", settings) is None
    assert resolve_caller(rt.graph, None, settings) is None


def test_unverified_mode_treats_token_as_configured_role():
    """No principals registered → a valid token gets unverified_token_role."""
    clear_principal_registry()
    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    settings = MCPSettings(tokens=TOKENS)
    caller = resolve_caller(rt.graph, "tok-owner", settings)
    assert caller["role"] == "owner"
    assert caller["verification"] == "identity_unverified"
    # ...and the operator can fail closed instead.
    strict = MCPSettings(tokens=TOKENS, unverified_token_role="")
    assert resolve_caller(rt.graph, "tok-owner", strict) is None


def test_handshake_is_open_but_carries_nothing(env):
    _, gateway, _ = env
    response = gateway.handle_jsonrpc(_rpc("initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "test"},
    }), token=None)
    assert response["result"]["serverInfo"]["name"] == "activegraph-assistant"
    assert gateway.handle_jsonrpc(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}, token=None
    ) is None


# ---------------------------------------------------------------- exposure


def test_default_exposure_owner_full_others_nothing(env):
    rt, gateway, _ = env
    owner_tools = gateway.handle_jsonrpc(_rpc("tools/list"), "tok-owner")
    names = {t["name"] for t in owner_tools["result"]["tools"]}
    assert names == {"chat", "memory_search", "util__echo"}

    agent_tools = gateway.handle_jsonrpc(_rpc("tools/list"), "tok-agent")
    assert agent_tools["result"]["tools"] == []

    anonymous = gateway.handle_jsonrpc(_rpc("tools/list"), None)
    assert anonymous["result"]["tools"] == []


def test_exposure_seeding_is_idempotent_and_respects_edits(env):
    rt, gateway, _ = env
    # Operator narrows chat to nobody; re-seeding must NOT resurrect it.
    set_exposure_fn(rt.graph, "chat", [], enabled=False, note="locked down")
    ensure_default_exposures(rt.graph, gateway.settings)
    assert not exposure_allows(rt.graph, "chat", "owner")
    chat_rules = [o for o in rt.graph.objects(type="mcp_exposure")
                  if o.data["surface"] == "chat"]
    assert len(chat_rules) == 1


def test_exposure_grants_are_role_scoped(env):
    rt, gateway, chat_calls = env
    set_exposure_fn(rt.graph, "chat", ["owner", "collaborator"], note="let the agent in")
    response = gateway.handle_jsonrpc(_tool_call("chat", {"message": "hi"}), "tok-agent")
    assert f"reply to {AGENT}" in _text(response)
    assert chat_calls[-1]["user_ref"] == AGENT  # caller identity reaches the pipeline


def test_per_tool_exposure_overrides_generic_tools_gate(env):
    rt, gateway, _ = env
    # Deny the specific tool while the generic 'tools' gate still allows owner.
    set_exposure_fn(rt.graph, "tool:util.echo", [], enabled=False, note="off")
    response = gateway.handle_jsonrpc(_tool_call("util__echo", {"text": "x"}), "tok-owner")
    assert "error" in response and response["error"]["code"] == -32003


# ---------------------------------------------------------------- dispatch


def test_chat_and_memory_dispatch_for_owner(env):
    rt, gateway, _ = env
    chat = gateway.handle_jsonrpc(_tool_call("chat", {"message": "hello"}), "tok-owner")
    assert f"reply to {OWNER}: hello" in _text(chat)

    memory = gateway.handle_jsonrpc(
        _tool_call("memory_search", {"query": "teal"}), "tok-owner")
    results = json.loads(_text(memory))["results"]
    assert OWNER in results[0]["text"]  # subject-scoped to the caller


def test_capability_call_runs_governed_path(env):
    rt, gateway, _ = env
    response = gateway.handle_jsonrpc(_tool_call("util__echo", {"text": "hi"}), "tok-owner")
    outcome = json.loads(_text(response))
    assert outcome["status"] == "done"
    assert '"echo": "hi"' in outcome["output"]
    (call,) = list(rt.graph.objects(type="capability_call"))
    assert call.data["proposed_by"] == f"mcp:{OWNER}"
    assert call.data["metadata"]["initiated_by"] == "mcp_inbound"


def test_high_risk_capability_is_held_then_approvable(env):
    rt, gateway, _ = env
    register_local_capability(
        "mail", "send", lambda text="": {"sent": text},
        input_schema=EchoInput, description="Send mail.", risk_class="high",
    )
    gateway.settings.expose_capabilities.append("mail.send")
    set_exposure_fn(rt.graph, "tool:mail.send", ["owner"], note="expose mail")

    response = gateway.handle_jsonrpc(_tool_call("mail__send", {"text": "memo"}), "tok-owner")
    outcome = json.loads(_text(response))
    assert outcome["status"] == "held_for_approval"

    # The hold resolves through the normal approval path and then executes.
    verdict = approve_capability_fn(rt.graph, outcome["call_id"], OWNER)
    assert verdict["ok"]
    rt.run_until_idle()
    call = rt.graph.get_object(outcome["call_id"])
    assert call.data["status"] == "done"


def test_refusals_and_grants_are_audited(env):
    rt, gateway, _ = env
    gateway.handle_jsonrpc(_tool_call("chat", {"message": "hi"}), "tok-owner")   # grant
    gateway.handle_jsonrpc(_tool_call("chat", {"message": "hi"}), "tok-agent")   # refusal
    gateway.handle_jsonrpc(_tool_call("chat", {"message": "hi"}), "tok-bogus")   # bad token

    access = [o.data for o in rt.graph.objects(type="mcp_access")
              if o.data["method"] == "tools/call"]
    assert [(a["caller"], a["allowed"]) for a in access] == [
        (OWNER, True), (AGENT, False), ("", False),
    ]


# ------------------------------------------------- governed config editing


def test_set_exposure_is_a_held_capability(env):
    """The agent-editable-config seam: mcp.set_exposure proposed through the
    gateway is HELD (high risk), and only after owner approval does the
    exposure rule actually change."""
    from packs.mcp.server import MCPGateway  # noqa: F401 (clarity)
    from packs.tool_gateway.gateway import decide_policy
    from packs.tool_gateway.llm_tools import llm_tools_for
    from activegraph.tools.context import ToolContext

    rt, gateway, _ = env
    (tool,) = llm_tools_for(rt.graph, ["mcp.set_exposure"])
    from packs.tool_gateway.tools import get_capability_spec
    model = get_capability_spec("mcp.set_exposure").input_schema

    ctx = ToolContext(behavior_name="chat_llm_responder", event_id="evt",
                      frame=None, idempotency_key="k", timeout_seconds=30.0)
    out = tool.fn(model(surface="memory_search", roles=["owner", "collaborator"],
                        note="let my research agent read memory"), ctx)
    assert out["status"] == "held_for_approval"
    assert not exposure_allows(rt.graph, "memory_search", "collaborator")  # not yet

    verdict = approve_capability_fn(rt.graph, out["call_id"], OWNER,
                                    note="approved: research agent may read")
    assert verdict["ok"]
    rt.run_until_idle()  # call_executor runs the approved edit

    assert exposure_allows(rt.graph, "memory_search", "collaborator")
    # Latest rule per surface wins (behavior-context edits append).
    rule = [o for o in rt.graph.objects(type="mcp_exposure")
            if o.data["surface"] == "memory_search"][-1]
    assert rule.data["note"] == "let my research agent read memory"
