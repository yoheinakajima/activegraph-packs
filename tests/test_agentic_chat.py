"""End-to-end test of agentic chat: the assistant bundle with a tool
allow-list, driven by a scripted provider (no API key).

Covers the whole seam chain:
  ChatSettings.tool_allow_list → bundles.assistant._chat_pack_for →
  packs.chat.build_pack → make_llm_responder(tools=…) → runtime tool loop →
  gateway proxy → capability_call/approval/result in the graph →
  grounded reply on the ChatTurn.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import pytest
from pydantic import BaseModel, Field

from activegraph.llm.types import LLMResponse, ToolCall

from bundles.assistant import build_assistant
from packs.chat import ChatSettings
from packs.chat.behaviors import clear_session_registry
from packs.chat.llm import ChatReply
from packs.chat.tools import submit_chat_input_fn
from packs.communication.behaviors import clear_thread_registry
from packs.tool_gateway.tools import clear_local_registry, register_local_capability


class LookupInput(BaseModel):
    company_name: str = Field(description="Company to look up.")


class ScriptedToolProvider:
    """Turn 1: request the crm tool. Turn 2: grounded final reply."""

    default_model = "scripted-1"

    def __init__(self) -> None:
        self.calls = 0
        self.tools_offered: list[str] = []

    def complete(self, **kw):
        self.calls += 1
        if self.calls == 1 and kw.get("tools"):
            self.tools_offered = [t.get("name") for t in (kw.get("tools") or [])]
            return LLMResponse(
                raw_text="", parsed=None, input_tokens=0, output_tokens=0,
                cost_usd=Decimal("0"), latency_seconds=0.0, model=self.default_model,
                finish_reason="tool_calls",
                tool_calls=[ToolCall(
                    id="c1", name="crm.lookup_company",
                    args={"company_name": "Northwind"},
                )],
            )
        reply = "Northwind's ARR is $2.4M (via CRM)."
        return LLMResponse(
            raw_text=json.dumps({"reply": reply}), parsed=ChatReply(reply=reply),
            input_tokens=0, output_tokens=0, cost_usd=Decimal("0"),
            latency_seconds=0.0, model=self.default_model, finish_reason="stop",
        )

    def estimate_cost(self, **kw) -> Decimal:
        return Decimal("0")

    def count_tokens(self, **kw) -> int:
        return 0

    def recognizes_model(self, name: str) -> bool:
        return True


@pytest.fixture(autouse=True)
def _isolate():
    clear_local_registry()
    clear_session_registry()
    clear_thread_registry()
    yield
    clear_local_registry()
    clear_session_registry()
    clear_thread_registry()


def test_agentic_chat_grounds_reply_in_gateway_tool_result():
    register_local_capability(
        "crm", "lookup_company",
        lambda company_name="": {"company": company_name, "arr": "$2.4M"},
        input_schema=LookupInput, description="Look up a company.", risk_class="low",
    )
    provider = ScriptedToolProvider()
    rt = build_assistant(
        chat_settings=ChatSettings(tool_allow_list=["crm.lookup_company"]),
        llm_provider=provider,
    )

    submit_chat_input_fn(rt.graph, user_ref="user:owner", content="What's Northwind's ARR?")
    rt.run_until_idle()

    # The model saw the proxy under its canonical gateway key.
    assert provider.tools_offered == ["crm.lookup_company"]

    # The reply is grounded in the tool result and reached the turn.
    turns = list(rt.graph.objects(type="chat_turn"))
    assert turns[-1].data["assistant_message"] == "Northwind's ARR is $2.4M (via CRM)."

    # The model-initiated action is a full gateway citizen.
    calls = list(rt.graph.objects(type="capability_call"))
    assert len(calls) == 1
    assert calls[0].data["status"] == "done"
    assert calls[0].data["metadata"]["initiated_by"] == "llm_tool_loop"
    assert len(list(rt.graph.objects(type="capability_result"))) == 1

    # The trace shows the loop: llm → tool → llm.
    kinds = [e.type for e in rt.graph.events if e.type.startswith(("llm.", "tool."))]
    assert kinds == [
        "llm.requested", "llm.responded",
        "tool.requested", "tool.responded",
        "llm.requested", "llm.responded",
    ]


def test_empty_allow_list_is_conversational_only():
    provider = ScriptedToolProvider()
    rt = build_assistant(chat_settings=ChatSettings(), llm_provider=provider)

    submit_chat_input_fn(rt.graph, user_ref="user:owner", content="hello")
    rt.run_until_idle()

    # No tools offered to the model, no tool round-trip, no capability_call —
    # exactly today's conversational behavior.
    assert provider.tools_offered == []
    assert list(rt.graph.objects(type="capability_call")) == []
    turns = list(rt.graph.objects(type="chat_turn"))
    assert turns[-1].data["assistant_message"] == "Northwind's ARR is $2.4M (via CRM)."
    kinds = [e.type for e in rt.graph.events if e.type.startswith(("llm.", "tool."))]
    assert kinds == ["llm.requested", "llm.responded"]
