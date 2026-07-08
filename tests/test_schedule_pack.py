"""Schedule Pack integration tests beyond the pack fixtures.

The fixture runner covers the pure time mechanics (fire/dedup/advance).
These tests cover the composition seams: the reminder capability flowing
through the Tool Gateway's LLM proxy (graph handle injected via
execution_context), and the tick that later delivers it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import pytest
from datetime import datetime, timedelta, timezone

from activegraph import Graph, Runtime
from activegraph.tools.context import ToolContext

from packs.communication import pack as comm_pack, CommunicationSettings
from packs.communication.behaviors import clear_thread_registry
from packs.core import pack as core_pack, CoreSettings
from packs.schedule import pack as schedule_pack, ScheduleSettings
from packs.schedule.capabilities import register_reminder_capability
from packs.schedule.tools import emit_due_ticks_fn
from packs.tool_gateway import pack as tg_pack, ToolGatewaySettings
from packs.tool_gateway.llm_tools import llm_tools_for
from packs.tool_gateway.tools import clear_local_registry


@pytest.fixture()
def rt():
    clear_local_registry()
    clear_thread_registry()
    register_reminder_capability()
    g = Graph()
    runtime = Runtime(g)
    runtime.load_pack(core_pack, settings=CoreSettings())
    runtime.load_pack(tg_pack, settings=ToolGatewaySettings(
        auto_approve_risk_classes=["low", "medium"],
    ))
    runtime.load_pack(comm_pack, settings=CommunicationSettings())
    runtime.load_pack(schedule_pack, settings=ScheduleSettings())
    yield runtime
    clear_local_registry()
    clear_thread_registry()


def _ctx() -> ToolContext:
    return ToolContext(
        behavior_name="chat.chat_llm_responder",
        event_id="evt_test",
        frame=None,
        idempotency_key="k",
        timeout_seconds=30.0,
    )


def test_reminder_via_gateway_proxy_then_tick_delivers(rt):
    from packs.schedule.capabilities import CreateReminderInput

    (tool,) = llm_tools_for(
        rt.graph, ["schedule.create_reminder"],
        settings=ToolGatewaySettings(auto_approve_risk_classes=["low", "medium"]),
    )

    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    out = tool.fn(CreateReminderInput(message="Standup!", at=soon, channel="chat"), _ctx())
    assert out["status"] == "done", out
    rt.run_until_idle()

    # The schedule exists, created through a fully recorded capability call.
    schedules = list(rt.graph.objects(type="schedule"))
    assert len(schedules) == 1
    assert schedules[0].data["kind"] == "once"
    calls = list(rt.graph.objects(type="capability_call"))
    assert calls and calls[0].data["capability_name"] == "create_reminder"
    assert calls[0].data["status"] == "done"

    # Time passes (edge-injected): the tick emits the approved candidate and
    # the Communication dispatcher marks it sent.
    later = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    ticks = emit_due_ticks_fn(rt.graph, later)
    assert len(ticks) == 1
    rt.run_until_idle()

    cands = list(rt.graph.objects(type="comm_response_candidate"))
    assert len(cands) == 1
    assert cands[0].data["content"] == "Standup!"
    assert cands[0].data["status"] == "sent"
    assert cands[0].data["metadata"]["reminder"] is True

    # 'once' auto-disabled; a later sweep does nothing.
    assert rt.graph.get_object(schedules[0].id).data["enabled"] is False
    assert emit_due_ticks_fn(rt.graph, later) == []


def test_reminder_requires_a_time(rt):
    from packs.schedule.capabilities import CreateReminderInput

    (tool,) = llm_tools_for(
        rt.graph, ["schedule.create_reminder"],
        settings=ToolGatewaySettings(auto_approve_risk_classes=["low", "medium"]),
    )
    out = tool.fn(CreateReminderInput(message="vague"), _ctx())
    assert out["status"] == "done"  # the call executed; the capability said no
    assert '"ok": false' in out["output"].lower()
    assert list(rt.graph.objects(type="schedule")) == []
