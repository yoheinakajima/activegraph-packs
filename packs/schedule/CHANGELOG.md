# Schedule Pack Changelog

## v0.1.0 — Initial release (2026-07-08)

### Added
- 3 object types: `schedule` (recurrence + payload declaration),
  `schedule_tick` (event-first record of a due moment, deduped by
  `{schedule_id}@{due_at}`), `heartbeat` (a pulse for housekeeping
  behaviors to subscribe to).
- 2 relation types: `tick_of`, `emitted_by`.
- 2 behaviors:
  - `tick_router` — instantiates the due schedule's payload object with
    schedule/tick provenance injected; unregistered payload types are
    recorded on the tick (`metadata.emit_skipped`), never silently dropped.
  - `schedule_bookkeeper` — advances `next_due_at` / `fire_count` /
    `last_fired_at`; `once` auto-disables. The graph owns schedule state,
    never the host driver.
- Tools: `create_schedule`, `emit_due_ticks` (the host-driver entry point;
  idempotent), `list_due_schedules`, `set_schedule_enabled`. Every function
  takes `now` as an argument — the pack owns no clock, so fixtures drive
  time with synthetic timestamps.
- `capabilities.register_reminder_capability()` — `schedule.create_reminder`
  as a Tool Gateway capability: "remind me tomorrow at 9am" becomes a
  one-turn, policy-governed chat tool call. The reminder payload is an
  approved `comm_response_candidate` the channel dispatcher delivers like
  any other outbound reply.
- Fixtures: interval fire/dedup/advance/refire, once + auto-disable, daily
  advance, catch-up (missed periods fire once) + disable, reminder →
  dispatched candidate. Tests: `tests/test_schedule_pack.py` (reminder via
  the gateway LLM proxy, then tick delivery).

### Design decisions
- No clock, no thread, no loop in the pack: wall-clock time enters the
  graph at the edge (demo server tick driver, cron, a worker) exactly like
  chat input. Proactive behavior without an orchestrator — a frame-emitter,
  not a coordinator.
- Catch-up policy `skip`: a driver down for an hour fires each missed
  schedule once and advances from the actual firing time.
