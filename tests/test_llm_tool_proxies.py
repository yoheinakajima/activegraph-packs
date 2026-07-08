"""Unit tests for the Tool Gateway's LLM tool proxies (llm_tools.py).

The proxies' contract: a model-callable Tool whose every invocation is a
graph-recorded, policy-checked capability call. Auto-approvable calls
execute inline (result in the same tool turn, approval recorded, exactly
one execution); held calls stay at policy_checking and resolve through the
normal approve/deny path.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import pytest
from pydantic import BaseModel, Field

from activegraph import Graph, Runtime
from activegraph.tools.context import ToolContext

from packs.core import pack as core_pack
from packs.tool_gateway import pack as tg_pack, ToolGatewaySettings
from packs.tool_gateway.llm_tools import as_llm_tool, llm_tools_for
from packs.tool_gateway.tools import (
    approve_capability_fn,
    clear_local_registry,
    deny_capability_fn,
    get_capability_spec,
    pending_approvals_fn,
    register_local_capability,
)


class EchoInput(BaseModel):
    text: str = Field(description="Text to echo.")


def _ctx() -> ToolContext:
    return ToolContext(
        behavior_name="test_behavior",
        event_id="evt_test",
        frame=None,
        idempotency_key="k",
        timeout_seconds=30.0,
    )


@pytest.fixture()
def gateway_rt():
    clear_local_registry()
    register_local_capability(
        "util", "echo", lambda text="": {"echo": text},
        input_schema=EchoInput, description="Echo text back.", risk_class="low",
    )
    register_local_capability(
        "mail", "send", lambda text="": {"sent": text},
        input_schema=EchoInput, description="Send mail.", risk_class="high",
    )
    register_local_capability(
        "raw", "no_schema", lambda text="": {"ok": True},
    )
    settings = ToolGatewaySettings(auto_approve_risk_classes=["low", "medium"])
    g = Graph()
    rt = Runtime(g)
    rt.load_pack(core_pack)
    rt.load_pack(tg_pack, settings=settings)
    yield rt, settings
    clear_local_registry()


def test_auto_approved_call_executes_inline_exactly_once(gateway_rt):
    rt, settings = gateway_rt
    (tool,) = llm_tools_for(rt.graph, ["util.echo"], settings=settings)

    out = tool.fn(EchoInput(text="hi"), _ctx())
    assert out["status"] == "done"
    assert '"echo": "hi"' in out["output"]

    # Settle: call_recorder / result_sourcer react; call_executor must NOT
    # re-execute (the approval was recorded after the call was already done).
    rt.run_until_idle()
    results = list(rt.graph.objects(type="capability_result"))
    assert len(results) == 1

    call = rt.graph.get_object(out["call_id"])
    assert call.data["status"] == "done"
    assert call.data["metadata"]["initiated_by"] == "llm_tool_loop"

    approvals = list(rt.graph.objects(type="capability_approval"))
    assert len(approvals) == 1
    assert approvals[0].data["policy_decision"] == "auto_approved"
    assert approvals[0].data["metadata"]["executed"] == "inline"

    # The result was bridged to Core (result_sourcer).
    sources = [o for o in rt.graph.objects(type="source")
               if o.data.get("kind") == "tool_result"]
    assert len(sources) == 1


def test_held_call_waits_then_executes_on_approval(gateway_rt):
    rt, settings = gateway_rt
    (tool,) = llm_tools_for(rt.graph, ["mail.send"], settings=settings)

    out = tool.fn(EchoInput(text="the memo"), _ctx())
    assert out["status"] == "held_for_approval"
    rt.run_until_idle()

    assert rt.graph.get_object(out["call_id"]).data["status"] == "policy_checking"
    assert list(rt.graph.objects(type="capability_result")) == []
    assert [p["call_id"] for p in pending_approvals_fn(rt.graph)] == [out["call_id"]]

    verdict = approve_capability_fn(rt.graph, out["call_id"], approver_ref="user:owner")
    assert verdict["ok"], verdict["reason"]
    rt.run_until_idle()

    assert rt.graph.get_object(out["call_id"]).data["status"] == "done"
    assert len(list(rt.graph.objects(type="capability_result"))) == 1


def test_held_call_denial_never_executes(gateway_rt):
    rt, settings = gateway_rt
    (tool,) = llm_tools_for(rt.graph, ["mail.send"], settings=settings)

    out = tool.fn(EchoInput(text="the memo"), _ctx())
    rt.run_until_idle()

    verdict = deny_capability_fn(
        rt.graph, out["call_id"], approver_ref="user:owner", reason="Not now."
    )
    assert verdict["ok"], verdict["reason"]
    rt.run_until_idle()

    assert rt.graph.get_object(out["call_id"]).data["status"] == "rejected"
    assert list(rt.graph.objects(type="capability_result")) == []
    denials = list(rt.graph.objects(type="capability_denial"))
    assert len(denials) == 1 and denials[0].data["reason"] == "Not now."


def test_unknown_capability_key_fails_loud(gateway_rt):
    rt, settings = gateway_rt
    with pytest.raises(ValueError, match="No capability registered under 'nope.missing'"):
        llm_tools_for(rt.graph, ["nope.missing"], settings=settings)


def test_capability_without_schema_cannot_be_llm_exposed(gateway_rt):
    rt, settings = gateway_rt
    spec = get_capability_spec("raw.no_schema")
    with pytest.raises(ValueError, match="no input_schema"):
        as_llm_tool(rt.graph, spec, settings=settings)
