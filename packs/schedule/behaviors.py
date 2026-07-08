"""Schedule Pack behaviors — v0.1.

Two behaviors, both reacting to schedule_tick.created — the pack's entire
runtime surface. The tick is the event; everything else is ordinary
reactive composition:

  tick_router          — instantiates the schedule's payload object. A
                         reminder payload emits a comm_response_candidate a
                         channel adapter dispatches; a heartbeat payload
                         emits a heartbeat pulse housekeeping behaviors
                         subscribe to; a capability payload emits a
                         capability_call the Tool Gateway governs. The
                         router doesn't know or care — it emits, the graph
                         coordinates.

  schedule_bookkeeper  — advances the schedule (last_fired_at, fire_count,
                         next_due_at; 'once' auto-disables). The GRAPH owns
                         schedule state; the host driver only supplies
                         timestamps.

No clock, no thread, no loop lives here — see tools.emit_due_ticks for how
time enters the graph.
"""

from __future__ import annotations

from activegraph.packs import behavior

from .settings import ScheduleSettings
from .tools import compute_next_due


@behavior(
    name="tick_router",
    on=["object.created"],
    where={"object.type": "schedule_tick"},
    creates=[],  # emits the schedule's declared payload type — open by design
)
def tick_router(event, graph, ctx, *, settings: ScheduleSettings):
    """Instantiate the due schedule's payload object.

    On: object.created (schedule_tick)
    Creates: one object of schedule.payload_emit_type, with
             metadata.schedule_id / tick_id / fired_at injected
    Relations: emitted_by (payload → schedule)

    Skips disabled schedules (a tick emitted just before a disable lands
    must not act) and payloads whose type isn't registered (the schedule
    outlived the pack that owned its payload — recorded as skipped in the
    tick's metadata rather than silently lost).
    """
    obj = event.payload.get("object", {})
    tick_id = obj.get("id")
    data = obj.get("data", {})
    schedule_id = data.get("schedule_id")
    if not schedule_id:
        return

    try:
        schedule = graph.get_object(schedule_id)
    except Exception:
        schedule = None
    if schedule is None or not (schedule.data or {}).get("enabled", False):
        return

    sdata = schedule.data
    emit_type = sdata.get("payload_emit_type")
    payload = dict(sdata.get("payload_data") or {})

    # Traceability: every emitted object records the schedule and tick that
    # asked for it. Merged under metadata so payload_data stays the caller's.
    meta = dict(payload.get("metadata") or {})
    meta.update({
        "schedule_id": schedule_id,
        "tick_id": tick_id,
        "fired_at": data.get("fired_at"),
    })
    payload["metadata"] = meta
    if "frame_id" not in payload:
        payload["frame_id"] = data.get("frame_id")

    # Heartbeats get their pulse sequence stamped for free.
    if emit_type == "heartbeat":
        payload.setdefault("fired_at", data.get("fired_at"))
        payload.setdefault("sequence", int(sdata.get("fire_count", 0)) + 1)
        payload.setdefault("schedule_id", schedule_id)

    try:
        emitted = graph.add_object(emit_type, payload)
    except Exception as exc:
        # Payload type not registered / schema mismatch: record why on the
        # tick — a schedule that can't act must fail visibly, not silently.
        try:
            graph.patch_object(tick_id, {"metadata": {
                **(data.get("metadata") or {}),
                "emit_skipped": f"{type(exc).__name__}: {exc}"[:300],
            }})
        except Exception:
            pass
        return

    # NOTE: add_relation signature is (source, target, type).
    try:
        graph.add_relation(emitted.id, schedule_id, "emitted_by")
    except Exception:
        pass


@behavior(
    name="schedule_bookkeeper",
    on=["object.created"],
    where={"object.type": "schedule_tick"},
    creates=[],
)
def schedule_bookkeeper(event, graph, ctx, *, settings: ScheduleSettings):
    """Advance the schedule a tick just satisfied.

    On: object.created (schedule_tick)
    Patches: schedule.last_fired_at / fire_count / next_due_at
             ('once' → enabled=False; nothing further is ever due)

    Catch-up policy: next_due_at advances from the tick's fired_at, not
    from the satisfied due moment — a driver that was down for an hour
    fires each missed schedule once and moves on, instead of replaying
    every missed period (settings.catch_up='skip', the only mode in v0.1).
    """
    obj = event.payload.get("object", {})
    data = obj.get("data", {})
    schedule_id = data.get("schedule_id")
    fired_at = data.get("fired_at")
    if not schedule_id or not fired_at:
        return

    try:
        schedule = graph.get_object(schedule_id)
    except Exception:
        schedule = None
    if schedule is None:
        return
    sdata = schedule.data or {}

    patch = {
        "last_fired_at": fired_at,
        "fire_count": int(sdata.get("fire_count", 0)) + 1,
    }
    if sdata.get("kind") == "once":
        patch["next_due_at"] = None
        patch["enabled"] = False
    else:
        patch["next_due_at"] = compute_next_due(
            sdata.get("kind", ""),
            every_seconds=sdata.get("every_seconds"),
            at_time=sdata.get("at_time"),
            at=sdata.get("at"),
            after=fired_at,
        )
    try:
        graph.patch_object(schedule_id, patch)
    except Exception:
        pass


BEHAVIORS = [tick_router, schedule_bookkeeper]
