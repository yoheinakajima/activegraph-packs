# Agent Profile Pack Changelog

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
- 7 object types: `agent_profile`, `goal`, `standing_instruction`, `personality_profile`, `owner_preference`, `profile_context_request`, `profile_context_view`
- 4 relation types: `owns_goal`, `owns_instruction`, `owns_preference`, `fulfilled_by_profile`
- 7 behaviors:
  - `profile_registry_recorder` — indexes `agent_profile` in local registry on creation
  - `goal_registry_recorder` — indexes `goal` by `profile_id` in local registry
  - `instruction_registry_recorder` — indexes `standing_instruction` by `profile_id`
  - `personality_registry_recorder` — indexes `personality_profile` by `profile_id`
  - `preference_registry_recorder` — indexes `owner_preference` by `profile_id`
  - `profile_context_provider` — assembles a filtered `profile_context_view` on `profile_context_request.created`; filters by `channel` and `audience_role`; suppresses mission from external contacts unless `expose_mission_to_external=True`
- `AgentProfileSettings` with `default_agent_name`, `default_mission`, `default_tone`, `default_verbosity`, `default_formality`, `owner_name`, `expose_mission_to_external`, `max_standing_instructions`, `max_active_goals`
- Tool functions: `register_profile_fn`, `request_profile_context_fn`
- Fixture scenarios: context assembly for owner vs. external audiences
- Full README with behavior map

### Design decisions
- Uses local in-memory registry (not `graph.objects()`) for all context assembly — behaviors are re-entrant safe
- Context is assembled on-demand via `profile_context_request`; no global system-prompt blob
- Instructions are sorted by `priority` (highest first) and filtered by `channel` + `audience_role` at request time
- Pack composes with Identity Pack via `audience_role` from `Principal.role`
