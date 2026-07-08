# Identity/Auth Pack Changelog

## v0.2.0 — Explicit registration + behavior-safe lookup (2026-07-08)

### Added
- `register_principal` / `register_principal_fn` — create or promote a
  Principal with an explicit role (seed the owner, promote a contact to
  collaborator). Idempotent by normalized sender_ref; keeps the in-process
  dedup registry in sync so resolvers and reply gating see the change
  immediately. The explicit counterpart to the automatic resolvers.
- `resolve_known_principal(graph, sender_ref)` — behavior-safe principal
  lookup (registry + get_object; no graph scans). This is the lookup channel
  adapters use for reply gating and audience-aware context
  (packs/communication/gating.py).

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
- 5 object types: `principal`, `auth_context`, `role`, `permission`, `delegation`
- 5 relation types: `resolves_to`, `authenticated_by`, `granted_by`, `granted_to`, `linked_to_entity`
- 5 behaviors:
  - `principal_resolver` — on `source.created`: resolves `sender_ref` to a `Principal`; assigns `role="owner"` if sender_ref matches `owner_identifiers`, else `default_external_role`; deduplicates via `_PRINCIPAL_REGISTRY` (patches `last_seen_at` + increments `seen_count` on revisit)
  - `comm_message_principal_resolver` — on `comm_message.created`: same resolution logic as `principal_resolver` but consuming CommMessage objects; shares `_PRINCIPAL_REGISTRY` so both paths converge on the same Principals
  - `auth_context_builder` — on `principal.created`: creates `auth_context` snapshotting `principal_role` + `channel` at resolution time; fires only on new principals (not on revisit patches)
  - `permission_checker` — on `action.created (status=proposed)`: rejects actions from `blocked` principals unconditionally; rejects high/critical risk actions for non-owner/admin roles; rejects execute-type actions for collaborator/external/unknown roles; patches `metadata.permission_checked=True` on pass
  - `principal_entity_linker` — on `principal.created`: soft-imports `_ENTITY_REGISTRY` from Entity Pack; links principal to existing entity via exact identifier overlap; patches `principal.entity_id` + creates `linked_to_entity` relation; silently no-ops if Entity Pack is not loaded
- `IdentitySettings` with `owner_identifiers`, `default_external_role`, `owner_auth_confidence`, `default_auth_confidence`, `create_auth_context`, `check_permissions_on_proposed_actions`, `auto_deduplicate_principals`
- `rebuild_principal_registry(graph)` utility for re-populating registry after `Runtime.load()` resume from SQLite event log
- Role hierarchy: `owner → admin → collaborator → external → customer → unknown → blocked`
- Full README with role hierarchy table, behavior map, and known limitations

### Design decisions
- `_PRINCIPAL_REGISTRY` is module-level state keyed by normalized `sender_ref`; must be cleared between tests with `clear_principal_registry()`
- `auth_context_builder` snapshots role at creation time so downstream behaviors don't re-fetch the principal on every action check
- `principal_entity_linker` uses a soft import (`try/except ImportError`) so Identity Pack remains loadable without Entity Pack
- `permission_checker` only fires when `principal_id` is present in `action.metadata` — actions without a principal context are not rejected
