# Secrets Pack Changelog

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
- 2 object types: `credential_ref`, `secret_usage_event`
- 1 relation type: `credential_used_in`
- 1 behavior: `secret_usage_recorder` — creates audit events on credential registration
- `resolve_credential` tool — reads actual secret from env var at execution time
- `SecretsSettings` with env_prefix, record_usage_events, fail_on_missing
- Fixture: credential_registration (registers ref → SecretUsageEvent created)
- Full README with security design diagram

### Design decisions
- CredentialRef contains name ONLY — never the actual secret value
- v0.1 supports environment variables only; v0.2 will add vault backend
- Usage events record registration (not resolution) — resolution events added in v0.2
