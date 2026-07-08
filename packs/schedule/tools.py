"""Schedule Pack tools — v0.1.

The clock stays at the edge: every function that needs to know what time it
is takes ``now`` as an argument (an ISO 8601 string or datetime). A host
driver — the demo server's tick thread, a cron job, a worker — calls
``emit_due_ticks(graph, now)`` periodically; fixtures call it with synthetic
times. Nothing in this pack ever reads the wall clock on its own.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, Optional, Union

from activegraph.packs import tool

TimeLike = Union[str, datetime]


def _as_dt(now: TimeLike) -> datetime:
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def compute_next_due(
    kind: str,
    *,
    every_seconds: Optional[int],
    at_time: Optional[str],
    at: Optional[str],
    after: TimeLike,
) -> Optional[str]:
    """Next due moment strictly after *after*, per the schedule spec.

    Returns None for a 'once' schedule whose moment has passed (nothing
    further is ever due). Shared by create_schedule (initial due) and
    schedule_bookkeeper (advance after a tick) so the two cannot drift.
    """
    ref = _as_dt(after)
    if kind == "interval":
        if not every_seconds:
            return None
        return _iso(ref + timedelta(seconds=int(every_seconds)))
    if kind == "daily":
        if not at_time:
            return None
        hh, mm = (int(p) for p in at_time.split(":", 1))
        candidate = ref.astimezone(timezone.utc).replace(
            hour=hh, minute=mm, second=0, microsecond=0
        )
        if candidate <= ref:
            candidate += timedelta(days=1)
        return _iso(candidate)
    if kind == "once":
        if not at:
            return None
        moment = _as_dt(at)
        return _iso(moment) if moment > ref else None
    return None


# ------------------------------------------------------------------ raw functions


def create_schedule_fn(
    graph,
    *,
    name: str,
    kind: str,
    payload_emit_type: str,
    payload_data: Optional[dict[str, Any]] = None,
    every_seconds: Optional[int] = None,
    at_time: Optional[str] = None,
    at: Optional[str] = None,
    now: TimeLike,
    enabled: bool = True,
    created_by: Optional[str] = None,
    frame_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
):
    """Create a Schedule with its initial next_due_at computed from *now*.

    For kind='once' the moment itself is the first due time (not "after
    now" advanced past it), so `at` in the future fires exactly once at
    `at`. Returns the schedule object.
    """
    if kind == "once":
        next_due = _iso(_as_dt(at)) if at and _as_dt(at) > _as_dt(now) else None
    else:
        next_due = compute_next_due(
            kind, every_seconds=every_seconds, at_time=at_time, at=at, after=now,
        )
    return graph.add_object("schedule", {
        "name": name,
        "kind": kind,
        "every_seconds": every_seconds,
        "at_time": at_time,
        "at": at,
        "payload_emit_type": payload_emit_type,
        "payload_data": payload_data or {},
        "enabled": enabled and next_due is not None,
        "next_due_at": next_due,
        "last_fired_at": None,
        "fire_count": 0,
        "created_by": created_by,
        "frame_id": frame_id,
        "metadata": metadata or {},
    })


def list_due_fn(graph, now: TimeLike) -> list[dict[str, Any]]:
    """Enabled schedules whose next_due_at is at or before *now*."""
    ref = _as_dt(now)
    due = []
    try:
        for obj in graph.objects(type="schedule"):
            data = obj.data or {}
            if not data.get("enabled") or not data.get("next_due_at"):
                continue
            if _as_dt(data["next_due_at"]) <= ref:
                due.append({"schedule_id": obj.id, **data})
    except Exception:
        pass
    due.sort(key=lambda s: s.get("next_due_at") or "")
    return due


def emit_due_ticks_fn(graph, now: TimeLike) -> list[str]:
    """Create a schedule_tick for every due schedule. Idempotent.

    The one call a host driver makes. Each tick's dedup_key is
    '{schedule_id}@{next_due_at}', so re-invoking with the same clock (or a
    twitchy driver double-firing) cannot double-emit — an existing tick with
    the same key is skipped. next_due_at is NOT advanced here; that is
    schedule_bookkeeper's job when the tick event lands (the graph owns
    schedule state). Returns the created tick ids.
    """
    fired_iso = _iso(_as_dt(now))
    existing_keys = set()
    try:
        for t in graph.objects(type="schedule_tick"):
            existing_keys.add((t.data or {}).get("dedup_key"))
    except Exception:
        pass

    created: list[str] = []
    for due in list_due_fn(graph, now):
        key = f"{due['schedule_id']}@{due['next_due_at']}"
        if key in existing_keys:
            continue
        tick = graph.add_object("schedule_tick", {
            "schedule_id": due["schedule_id"],
            "fired_at": fired_iso,
            "due_at": due["next_due_at"],
            "dedup_key": key,
            "frame_id": None,
            "metadata": {},
        })
        # NOTE: add_relation signature is (source, target, type).
        try:
            graph.add_relation(tick.id, due["schedule_id"], "tick_of")
        except Exception:
            pass
        created.append(tick.id)
    return created


def set_schedule_enabled_fn(graph, schedule_id: str, enabled: bool) -> dict[str, Any]:
    """Enable or disable a schedule. Returns {'ok': bool, 'reason': str}."""
    try:
        obj = graph.get_object(schedule_id)
    except Exception:
        obj = None
    if obj is None or obj.type != "schedule":
        return {"ok": False, "reason": f"no schedule {schedule_id!r}"}
    graph.patch_object(schedule_id, {"enabled": bool(enabled)})
    return {"ok": True, "reason": "updated"}


# ------------------------------------------------------------------ tool wrappers


@tool(
    name="create_schedule",
    description=(
        "Create a schedule (interval | daily | once) that emits a declared "
        "object on each firing. next_due_at is computed from the supplied "
        "'now' — the caller owns the clock."
    ),
)
def create_schedule(
    graph,
    name: str,
    kind: str,
    payload_emit_type: str,
    now: str,
    payload_data: Optional[dict] = None,
    every_seconds: Optional[int] = None,
    at_time: Optional[str] = None,
    at: Optional[str] = None,
):
    """Registered tool wrapper — delegates to create_schedule_fn."""
    return create_schedule_fn(
        graph, name=name, kind=kind, payload_emit_type=payload_emit_type,
        payload_data=payload_data, every_seconds=every_seconds,
        at_time=at_time, at=at, now=now,
    )


@tool(
    name="emit_due_ticks",
    description=(
        "Create a schedule_tick for every schedule due at 'now' (idempotent "
        "by '{schedule_id}@{due_at}'). THE host-driver entry point: call "
        "periodically with the current time, then settle the runtime."
    ),
)
def emit_due_ticks(graph, now: str) -> list[str]:
    """Registered tool wrapper — delegates to emit_due_ticks_fn."""
    return emit_due_ticks_fn(graph, now)


@tool(
    name="list_due_schedules",
    description="List enabled schedules due at or before 'now'.",
)
def list_due_schedules(graph, now: str) -> list[dict]:
    """Registered tool wrapper — delegates to list_due_fn."""
    return list_due_fn(graph, now)


@tool(
    name="set_schedule_enabled",
    description="Enable or disable a schedule by id.",
)
def set_schedule_enabled(graph, schedule_id: str, enabled: bool) -> dict:
    """Registered tool wrapper — delegates to set_schedule_enabled_fn."""
    return set_schedule_enabled_fn(graph, schedule_id, enabled)


TOOLS = [create_schedule, emit_due_ticks, list_due_schedules, set_schedule_enabled]
