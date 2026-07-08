# Schedule Pack v0.1

> Time, made graph-native. Proactive behavior without an orchestrator.

## Purpose

An always-on assistant must act when *time* passes, not only when a message
arrives. This pack makes that graph-native: a `schedule` declares WHEN to
fire and WHAT object to emit; a `schedule_tick` is the event-first record
that a due moment arrived; ordinary behaviors react to the tick. **The pack
owns no clock, no thread, and no loop** — a host driver injects timestamps
through the `emit_due_ticks` tool, exactly the way chat input is injected.
That single decision is what keeps every fixture deterministic and the pack
pure-reactive: "always-on" is a schedule row plus a ~20-line driver, not an
architecture change.

## Behavior Map

```
[host driver: emit_due_ticks(graph, now)]        ← the ONLY place time enters
  → schedule_tick.created   (deduped by "{schedule_id}@{due_at}")
      → tick_router
          creates: one object of schedule.payload_emit_type
                   (metadata.schedule_id / tick_id / fired_at injected)
          relations: emitted_by (payload → schedule)
      → schedule_bookkeeper
          patches: schedule.last_fired_at / fire_count / next_due_at
                   ("once" auto-disables — nothing further is ever due)
```

Everything downstream is ordinary reactive composition: the router emits,
the graph coordinates.

## Object Types

| Type | Description | Key Fields |
|------|-------------|------------|
| `schedule` | A declared recurrence + payload | `kind` (interval/daily/once), `every_seconds`, `at_time`, `at`, `payload_emit_type`, `payload_data`, `enabled`, `next_due_at`, `fire_count` |
| `schedule_tick` | Event-first record of a due moment | `schedule_id`, `fired_at`, `due_at`, `dedup_key` |
| `heartbeat` | A pulse for housekeeping behaviors to subscribe to | `fired_at`, `sequence`, `schedule_id` |

## Relation Types

| Relation | Source → Target | Description |
|----------|-----------------|-------------|
| `tick_of` | schedule_tick → schedule | A tick satisfies a schedule's due moment |
| `emitted_by` | (any) → schedule | A payload object was emitted by a schedule's tick |

## Payload recipes

The router emits whatever object type the schedule declares — composition
recipes, not special cases:

- **Reminder** — `comm_response_candidate` (status=approved): when due, the
  channel's dispatcher delivers it exactly like any other approved outbound
  reply. Reminders are ordinary messages that happen to originate from a tick.
- **Heartbeat** — `heartbeat`: housekeeping behaviors (memory reflection
  passes, follow-up checks) declare `where={"object.type": "heartbeat"}` and
  get proactive work with no pack owning a loop.
- **Scheduled action** — `capability_call` (status=proposed): governed by the
  Tool Gateway's policy/approval/audit like any other proposal.

## Dependencies

```python
requires = ["core"]
integrates_with = ["communication", "tool_gateway"]
# communication — reminder delivery via response_dispatcher
# tool_gateway  — scheduled capability proposals; the reminder capability
```

## Usage

```python
from activegraph import Runtime, Graph
from packs.core import pack as core_pack
from packs.schedule import pack as schedule_pack
from packs.schedule.tools import create_schedule_fn, emit_due_ticks_fn

rt = Runtime(Graph())
rt.load_pack(core_pack)
rt.load_pack(schedule_pack)

create_schedule_fn(rt.graph, name="pulse", kind="interval", every_seconds=300,
                   payload_emit_type="heartbeat", now="2026-07-08T09:00:00Z")

# The host driver loop (cron, a thread, a worker):
emit_due_ticks_fn(rt.graph, "2026-07-08T09:05:00Z")
rt.run_until_idle()
```

The demo server ships the reference driver: a daemon thread sweeping every
`SCHEDULE_TICK_SECONDS` (default 10; `<=0` disables), serialized with the
HTTP handlers via the runtime lock.

## "Remind me tomorrow at 9am"

`capabilities.register_reminder_capability()` registers
`schedule.create_reminder` on the Tool Gateway. Add it to
`ChatSettings.tool_allow_list` and the LLM schedules reminders through the
gateway — recorded, policy-checked, audited. The handler receives the graph
via the gateway's `execution_context` at execution time, so registration is
graph-free.

## Design notes

- **The graph owns schedule state.** `next_due_at` advances in
  `schedule_bookkeeper` (a behavior reacting to the tick), never in the
  driver. Drivers are stateless and disposable.
- **Idempotent emission.** Tick dedup keys (`schedule_id@due_at`) make a
  twitchy or restarted driver harmless.
- **Catch-up = skip.** A driver that was down for an hour fires each missed
  schedule once and advances from the actual firing time — no replaying
  missed periods (`ScheduleSettings.catch_up`, the only mode in v0.1).
- **Failed payloads fail visibly.** A payload type that isn't registered is
  recorded on the tick (`metadata.emit_skipped`), never silently dropped.

## Fixtures

```bash
python packs/schedule/fixtures/run_fixtures.py
```

Five scenarios, all driven with synthetic timestamps — no wall clock, no
sleeps, no API key: interval fire/dedup/advance/refire; once + auto-disable;
daily advance; catch-up + disable; reminder → dispatched candidate.

## CHANGELOG

See [`CHANGELOG.md`](CHANGELOG.md).
