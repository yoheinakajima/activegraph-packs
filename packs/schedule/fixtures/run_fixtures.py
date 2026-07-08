"""Run Schedule Pack fixture scenarios.

Every scenario drives time with SYNTHETIC timestamps through
emit_due_ticks_fn — no wall clock, no sleeps, no API key. That is the
pack's core promise: the clock lives at the edge, so time-driven behavior
is exactly as deterministic as any other fixture.

Usage:
    python packs/schedule/fixtures/run_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parents[2]))

from activegraph import Graph, Runtime
from packs.core import pack as core_pack, CoreSettings
from packs.schedule import pack as schedule_pack, ScheduleSettings
from packs.schedule.tools import (
    create_schedule_fn,
    emit_due_ticks_fn,
    list_due_fn,
    set_schedule_enabled_fn,
)

T0 = "2026-07-08T09:00:00+00:00"
T0_PLUS_5M = "2026-07-08T09:05:00+00:00"
T0_PLUS_10M = "2026-07-08T09:10:00+00:00"
NEXT_DAY = "2026-07-09T09:30:00+00:00"


def _runtime():
    g = Graph()
    rt = Runtime(g)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(schedule_pack, settings=ScheduleSettings())
    return g, rt


def run_interval_fixture() -> dict:
    """Interval schedule: fires when due, dedups re-fires, advances, refires."""
    g, rt = _runtime()
    sched = create_schedule_fn(
        g, name="pulse", kind="interval", every_seconds=300,
        payload_emit_type="heartbeat", now=T0,
    )
    rt.run_until_idle()

    # Not yet due at T0 (first due is T0+300s).
    assert emit_due_ticks_fn(g, T0) == [], "must not fire before due"

    # Due at T0+5m → one tick, one heartbeat, bookkeeping advanced.
    ticks = emit_due_ticks_fn(g, T0_PLUS_5M)
    assert len(ticks) == 1, f"expected 1 tick, got {len(ticks)}"
    rt.run_until_idle()

    beats = list(g.objects(type="heartbeat"))
    assert len(beats) == 1, f"expected 1 heartbeat, got {len(beats)}"
    assert beats[0].data["sequence"] == 1
    assert beats[0].data["metadata"]["schedule_id"] == sched.id

    updated = g.get_object(sched.id).data
    assert updated["fire_count"] == 1
    assert updated["last_fired_at"].startswith("2026-07-08T09:05")
    assert updated["next_due_at"].startswith("2026-07-08T09:10")

    # Same clock again → idempotent (dedup_key), no second tick.
    assert emit_due_ticks_fn(g, T0_PLUS_5M) == [], "dedup must block re-fire"
    rt.run_until_idle()
    assert len(list(g.objects(type="heartbeat"))) == 1

    # Next period → fires again.
    assert len(emit_due_ticks_fn(g, T0_PLUS_10M)) == 1
    rt.run_until_idle()
    assert len(list(g.objects(type="heartbeat"))) == 2

    rels = [r for r in g.relations() if r.type == "emitted_by"]
    assert len(rels) == 2 and all(r.target == sched.id for r in rels)
    tick_rels = [r for r in g.relations() if r.type == "tick_of"]
    assert len(tick_rels) == 2

    return {"heartbeats": 2, "fire_count": g.get_object(sched.id).data["fire_count"]}


def run_once_fixture() -> dict:
    """'once' schedule: fires exactly once at its moment, then auto-disables."""
    g, rt = _runtime()
    sched = create_schedule_fn(
        g, name="one-shot", kind="once", at=T0_PLUS_5M,
        payload_emit_type="task",
        payload_data={"title": "Prepare the board deck", "status": "active"},
        now=T0,
    )
    rt.run_until_idle()

    assert len(emit_due_ticks_fn(g, T0_PLUS_5M)) == 1
    rt.run_until_idle()

    tasks = [t for t in g.objects(type="task")
             if t.data.get("title") == "Prepare the board deck"]
    assert len(tasks) == 1, f"expected the scheduled task, got {len(tasks)}"
    assert tasks[0].data["metadata"]["schedule_id"] == sched.id

    after = g.get_object(sched.id).data
    assert after["enabled"] is False, "'once' must auto-disable after firing"
    assert after["next_due_at"] is None

    # Later sweeps never fire it again.
    assert emit_due_ticks_fn(g, NEXT_DAY) == []
    return {"tasks": 1, "disabled": not after["enabled"]}


def run_daily_fixture() -> dict:
    """Daily schedule: due at HH:MM, advances a day after firing."""
    g, rt = _runtime()
    sched = create_schedule_fn(
        g, name="morning brief", kind="daily", at_time="09:30",
        payload_emit_type="heartbeat", now=T0,   # 09:00 → due today 09:30
    )
    rt.run_until_idle()

    assert g.get_object(sched.id).data["next_due_at"].startswith("2026-07-08T09:30")
    assert emit_due_ticks_fn(g, T0_PLUS_5M) == [], "09:05 is before 09:30"
    assert len(emit_due_ticks_fn(g, "2026-07-08T09:31:00+00:00")) == 1
    rt.run_until_idle()
    assert g.get_object(sched.id).data["next_due_at"].startswith("2026-07-09T09:30")

    return {"next_due": g.get_object(sched.id).data["next_due_at"]}


def run_catch_up_and_disable_fixture() -> dict:
    """Missed periods fire once (skip catch-up); disabled schedules stay silent."""
    g, rt = _runtime()
    sched = create_schedule_fn(
        g, name="pulse", kind="interval", every_seconds=300,
        payload_emit_type="heartbeat", now=T0,
    )
    rt.run_until_idle()

    # Driver was "down" for a day: ONE tick fires (not 288), and next_due_at
    # advances from the actual firing time.
    ticks = emit_due_ticks_fn(g, NEXT_DAY)
    assert len(ticks) == 1, "skip catch-up fires missed schedules once"
    rt.run_until_idle()
    assert g.get_object(sched.id).data["next_due_at"].startswith("2026-07-09T09:35")

    # Disabled → due moments pass silently.
    set_schedule_enabled_fn(g, sched.id, False)
    rt.run_until_idle()
    assert emit_due_ticks_fn(g, "2026-07-09T10:00:00+00:00") == []
    assert list_due_fn(g, "2026-07-09T10:00:00+00:00") == []

    return {"catch_up_ticks": 1}


def run_reminder_payload_fixture() -> dict:
    """The reminder recipe: a tick emits an approved comm_response_candidate.

    Loads the Communication Pack so the emitted candidate flows through
    response_dispatcher (status → sent) exactly like any other approved
    outbound reply. Reminders are ordinary messages that happen to
    originate from a tick.
    """
    from packs.communication import pack as comm_pack, CommunicationSettings
    from packs.communication.behaviors import clear_thread_registry

    clear_thread_registry()
    g = Graph()
    rt = Runtime(g)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(comm_pack, settings=CommunicationSettings())
    rt.load_pack(schedule_pack, settings=ScheduleSettings())

    create_schedule_fn(
        g, name="reminder: standup", kind="once", at=T0_PLUS_5M,
        payload_emit_type="comm_response_candidate",
        payload_data={
            "message_id": "",
            "channel": "chat",
            "content": "Reminder: daily standup in 10 minutes.",
            "status": "approved",
            "created_by_behavior": "schedule.tick_router",
            "metadata": {"reminder": True},
        },
        now=T0,
    )
    rt.run_until_idle()
    emit_due_ticks_fn(g, T0_PLUS_5M)
    rt.run_until_idle()

    cands = list(g.objects(type="comm_response_candidate"))
    assert len(cands) == 1
    assert cands[0].data["status"] == "sent", (
        f"dispatcher should mark the reminder sent, got {cands[0].data['status']}"
    )
    assert cands[0].data["metadata"]["reminder"] is True
    assert cands[0].data["metadata"]["schedule_id"]

    return {"reminder_status": cands[0].data["status"]}


def run_all() -> bool:
    print("=" * 60)
    print("Schedule Pack Fixtures")
    print("=" * 60)

    print("\n[1] interval schedule (fire / dedup / advance / refire)")
    r = run_interval_fixture()
    print(f"  PASS: heartbeats={r['heartbeats']}, fire_count={r['fire_count']}")

    print("\n[2] once schedule (single fire + auto-disable)")
    r = run_once_fixture()
    print(f"  PASS: tasks={r['tasks']}, disabled={r['disabled']}")

    print("\n[3] daily schedule (HH:MM due + day advance)")
    r = run_daily_fixture()
    print(f"  PASS: next_due={r['next_due']}")

    print("\n[4] catch-up (missed periods fire once) + disable")
    r = run_catch_up_and_disable_fixture()
    print(f"  PASS: catch_up_ticks={r['catch_up_ticks']}")

    print("\n[5] reminder payload (tick → approved candidate → dispatched)")
    r = run_reminder_payload_fixture()
    print(f"  PASS: reminder_status={r['reminder_status']}")

    print("\nALL PASS")
    return True


if __name__ == "__main__":
    try:
        ok = run_all()
    except AssertionError as e:
        print(f"\nFAIL: {e}")
        sys.exit(1)
    sys.exit(0 if ok else 1)
