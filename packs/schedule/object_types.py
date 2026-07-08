"""Schedule Pack object and relation types — v0.1.

Time, made graph-native. A `schedule` declares WHEN something should happen
and WHAT object to emit when it does; a `schedule_tick` is the event-first
record that a due moment arrived. The pack owns NO clock and NO thread —
wall-clock time enters the graph exactly the way chat input does: injected
at the edge (a host driver calls the emit_due_ticks tool with a timestamp).
That is what keeps every fixture deterministic and the pack pure-reactive.

Key design rules:
- The tick IS the event; behaviors react to it. No orchestrator loop.
- next_due_at is advanced by a behavior (schedule_bookkeeper), never by the
  driver — the graph, not the host, owns schedule state.
- Ticks carry a dedup_key so a driver that fires twice cannot double-emit.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from activegraph.packs import ObjectType, RelationType


# ================================================================ Schemas


class Schedule(BaseModel):
    """A declared recurrence: when to fire, and what to emit when firing.

    spec kinds:
      interval — every ``every_seconds`` seconds
      daily    — once a day at ``at_time`` ("HH:MM", UTC)
      once     — a single firing at ``at`` (ISO 8601); auto-disables after

    payload:
      ``emit_type`` names the object type to create on each tick and
      ``data`` is its body. The emitted object gets
      ``metadata.schedule_id/tick_id/fired_at`` injected so downstream
      behaviors can always trace an action back to the schedule that asked
      for it. Common recipes: a ``comm_response_candidate``
      (status=approved) that a channel adapter dispatches — a reminder that
      reaches the owner; a ``task``; a ``heartbeat``.
    """

    name: str = Field(description="Human-readable schedule name.")
    kind: Literal["interval", "daily", "once"] = Field(
        description="Recurrence kind: interval | daily | once."
    )
    every_seconds: Optional[int] = Field(
        default=None,
        ge=1,
        description="Firing period for kind='interval'.",
    )
    at_time: Optional[str] = Field(
        default=None,
        description="Time of day 'HH:MM' (UTC) for kind='daily'.",
    )
    at: Optional[str] = Field(
        default=None,
        description="ISO 8601 datetime for kind='once'.",
    )
    payload_emit_type: str = Field(
        description="Object type to create on each tick.",
    )
    payload_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Body of the emitted object. Must not contain secrets.",
    )
    enabled: bool = Field(default=True)
    next_due_at: Optional[str] = Field(
        default=None,
        description=(
            "ISO 8601 datetime of the next firing. Computed at creation and "
            "advanced by schedule_bookkeeper after each tick — the graph owns "
            "schedule state, never the host driver."
        ),
    )
    last_fired_at: Optional[str] = Field(default=None)
    fire_count: int = Field(default=0, ge=0)
    created_by: Optional[str] = Field(
        default=None,
        description="Who/what created this schedule (behavior, tool, user ref).",
    )
    frame_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScheduleTick(BaseModel):
    """The event-first record that a schedule's due moment arrived.

    Created by the emit_due_ticks tool (driven by a host with a timestamp).
    Everything that happens because time passed hangs off this object:
    tick_router emits the schedule's payload, schedule_bookkeeper advances
    the schedule. ``dedup_key`` (schedule_id @ due time) makes emission
    idempotent — a twitchy driver cannot double-fire a due moment.
    """

    schedule_id: str = Field(description="ID of the Schedule that was due.")
    fired_at: str = Field(description="ISO 8601 timestamp the driver passed in.")
    due_at: str = Field(description="The due moment this tick satisfies.")
    dedup_key: str = Field(description="'{schedule_id}@{due_at}' — idempotency key.")
    frame_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Heartbeat(BaseModel):
    """A periodic pulse other packs can subscribe to.

    The maintenance-frame pattern: a heartbeat schedule emits these, and
    housekeeping behaviors (memory reflection passes, follow-up checks)
    declare ``where={"object.type": "heartbeat"}`` — proactive work without
    any pack owning a loop. Deliberately empty of domain meaning.
    """

    fired_at: str = Field(description="ISO 8601 timestamp of this pulse.")
    sequence: int = Field(default=0, ge=0, description="Pulse count for this schedule.")
    schedule_id: Optional[str] = Field(default=None)
    frame_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ================================================================ ObjectType list

OBJECT_TYPES = [
    ObjectType(
        name="schedule",
        schema=Schedule,
        description=(
            "A declared recurrence: when to fire (interval/daily/once) and what "
            "object to emit on each firing. State (next_due_at, fire_count) is "
            "advanced by behaviors, never by the host driver."
        ),
    ),
    ObjectType(
        name="schedule_tick",
        schema=ScheduleTick,
        description=(
            "The event-first record that a schedule's due moment arrived — the "
            "trigger every time-driven behavior hangs off. Deduped by "
            "'{schedule_id}@{due_at}'."
        ),
    ),
    ObjectType(
        name="heartbeat",
        schema=Heartbeat,
        description=(
            "A periodic pulse for housekeeping behaviors to subscribe to "
            "(memory reflection, follow-up checks) — proactive work without an "
            "orchestrator loop."
        ),
    ),
]


# ================================================================ RelationType list

RELATION_TYPES = [
    RelationType(
        name="tick_of",
        source_types=("schedule_tick",),
        target_types=("schedule",),
        description="A tick satisfies a due moment of its schedule.",
    ),
    RelationType(
        name="emitted_by",
        # Source open (empty = any): a schedule may emit any declared object
        # type — enumerating them here would couple this pack to every
        # possible payload.
        source_types=(),
        target_types=("schedule",),
        description="An object was emitted by a schedule's tick (payload instantiation).",
    ),
]
