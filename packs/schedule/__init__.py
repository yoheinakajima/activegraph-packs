"""activegraph.packs.schedule — Schedule Pack v0.1.

Time, made graph-native: schedules declare WHEN to fire and WHAT object to
emit; ticks are the event-first record that a due moment arrived. The pack
owns no clock and no thread — a host driver (the demo server's tick
thread, cron, a worker) injects timestamps through the emit_due_ticks
tool, exactly the way chat input is injected. "Always-on" becomes a
schedule row, not an architecture change.

Object types: schedule, schedule_tick, heartbeat
Behaviors:    tick_router (payload emission), schedule_bookkeeper (state)
Tools:        create_schedule, emit_due_ticks, list_due_schedules,
              set_schedule_enabled
Capabilities: schedule.create_reminder (via capabilities.py — makes
              "remind me tomorrow at 9" a policy-governed chat tool call)

Behavior map:
  [host driver] emit_due_ticks(graph, now)
    → schedule_tick.created (deduped by '{schedule_id}@{due_at}')
        → tick_router          emits schedule.payload_emit_type
                               (+ emitted_by relation)
        → schedule_bookkeeper  advances next_due_at / fire_count
                               ('once' auto-disables)

Common payload recipes:
  comm_response_candidate(status=approved) — a reminder a channel adapter
    delivers like any other approved reply
  heartbeat — a pulse housekeeping behaviors subscribe to (memory
    reflection, follow-up checks)
  capability_call(status=proposed) — scheduled actions, governed by the
    Tool Gateway like any other proposal

Usage:
    from activegraph import Runtime, Graph
    from packs.core import pack as core_pack
    from packs.schedule import pack as schedule_pack
    from packs.schedule.tools import create_schedule_fn, emit_due_ticks_fn

    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    rt.load_pack(schedule_pack)

    create_schedule_fn(rt.graph, name="pulse", kind="interval",
                       every_seconds=300, payload_emit_type="heartbeat",
                       now="2026-07-08T09:00:00Z")
    # driver loop:
    emit_due_ticks_fn(rt.graph, "2026-07-08T09:05:00Z")
    rt.run_until_idle()

Entry point: registered as 'schedule' in [project.entry-points."activegraph.packs"]
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir
from activegraph.packs.manifest import CapabilityDecl

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import ScheduleSettings
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# requires=["core"], integrates_with=["communication", "tool_gateway"]
pack = Pack(
    name="schedule",
    version="0.2.0",
    description=(
        "Graph-native scheduling: schedules declare when to fire and what to "
        "emit; ticks are event-first records of due moments; a host driver "
        "injects timestamps at the edge. No clock, no thread, no loop in the "
        "pack — proactive behavior without an orchestrator."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=load_prompts_from_dir(_PROMPTS_DIR) if _PROMPTS_DIR.exists() else (),
    # Declarative capability surface (Q8 mechanism chain, step 1):
    # mirrors this pack's register_local_capability host wiring so the
    # loader's two-way surface check covers capabilities too. CI's AST
    # check (tests/test_manifests.py) keeps this honest against the code.
    capabilities=(
        CapabilityDecl(provider='schedule', capability='create_reminder', risk_class='low', credential_ref='', action_class='R3'),
    ),
    settings_schema=ScheduleSettings,
)

__all__ = ["pack", "ScheduleSettings"]
