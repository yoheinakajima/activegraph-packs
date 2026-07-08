# Communication Pack Changelog

## v0.2.1 — activegraph 1.3 compatibility (2026-07-08)

### Fixed
- `@tool` wrapper signatures satisfy the runtime's v1.3 registration-time
  validation: every parameter beyond the `(args, ctx)` invocation contract
  now has a default. No behavior change (behaviors call the `_fn`
  variants directly).

## v0.2.0 — Intent routing: detection is no longer decorative (2026-07-08)

### Added
- `intent_router` behavior — on `comm_intent.created`, proposes a Tool
  Gateway `capability_call` (status=proposed) when the intent kind has a
  configured route and detection confidence clears
  `intent_route_min_confidence`. The proposal enters the normal gateway
  lifecycle (policy check → approval/hold → execution → audit); this pack
  only proposes. Before this, nothing consumed `comm_intent` objects.
- `fulfills_intent` relation (capability_call → comm_intent) linking the
  proposed action back to the intent that motivated it.
- Settings: `intent_routes` (default `{}` — no routing) and
  `intent_route_min_confidence` (default 0.6).
- Fixture: `run_intent_router_fixture` (routed 'request' executes through
  the gateway; unrouted 'query' proposes nothing).

- `gating.py` — `decide_reply(graph, sender_ref, reply_policy=...)`: the one
  reply-gating decision shared by every channel adapter ('open' | 'known' |
  'owner_only'). Behavior-safe (identity registry + get_object, no scans),
  fail-closed for unverifiable senders, and 'blocked' principals are denied
  even under 'open'. Adapters stamp the verdict on the comm_message at
  ingestion so responders can match it declaratively in `where`.

### Design decisions
- Degrades gracefully: without the Tool Gateway Pack the `capability_call`
  type does not exist and the router no-ops — intents stay informational.

## v0.1.1 — Relation integrity fix (2026-07-08)

### Fixed
- **`add_relation` argument order.** Relation writes passed
  `(type, source, target)` but the API is `(source, target, type)` — the same
  bug the Chat Pack fixed in its v0.2.0. Affected relations were being written
  as garbage edges (the type string as the source id), silently breaking graph
  traversal over this pack's audit trail. Part of a repo-wide sweep (80 calls
  across 14 packs) that also corrected fixture assertions written against the
  broken shape (`r.source` where `r.type` was meant).

## v0.1.0 — Initial release (2026-06-03)

### Added
- 5 object types: `comm_thread`, `comm_message`, `comm_intent`, `comm_response_candidate`, `comm_participant`
- 6 relation types: `thread_contains`, `intent_of`, `response_to`, `participates_in`, `derived_from_source`, `dispatched_to`
- 3 behaviors:
  - `intent_detector` — on `comm_message.created (direction=inbound)`: heuristic keyword/pattern intent classification into 7 classes (`query`, `request`, `reply`, `notification`, `review`, `approval_request`, `unknown`); tie-breaking by specificity priority; no LLM required
  - `thread_tracker` — on `comm_message.created`: creates or resumes `CommThread` keyed by `(channel, thread_id_hint)` using `_THREAD_REGISTRY`; creates `comm_participant` for sender; patches `comm_message.thread_id`
  - `response_dispatcher` — on `comm_response_candidate.created (status=approved)`: creates `dispatched_to` relation; patches candidate status to `"sent"`
- `CommunicationSettings` with `intent_detection_mode`, `auto_create_threads`, `default_channel`, `low_confidence_intent_threshold`, `auto_dispatch_approved_responses`, `max_thread_participants`
- Tool functions: `create_comm_message_fn`
- Fixture scenarios: intent classification, thread tracking, multi-channel response dispatch
- Full README with intent signal table and behavior map

### Design decisions
- `intent_detector` is fully deterministic (no LLM) — usable in all fixture and production scenarios without API keys
- `thread_tracker` uses a module-level `_THREAD_REGISTRY` dict rather than `graph.objects()` scans for O(1) thread lookup; safe in behavior context
- `response_dispatcher` does not perform actual HTTP delivery — channel adapters (Chat, Email) handle transport
- Clear between tests: `clear_thread_registry()`
