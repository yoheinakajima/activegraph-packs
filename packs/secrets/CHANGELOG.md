# Secrets Pack Changelog

## v0.2.0 — Managed credentials (OAuth device flow) (2026-07-08)

### Added
- `managed.py` — managed credential sources behind the SAME
  `resolve_credential_fn` seam the gateway already uses. Environment
  still wins; registered sources are consulted in order; a broken
  source never blocks the chain.
  - `OAuthTokenStore`: SQLite persistence for tokens, in a separate
    file from every graph store because it holds secret VALUES. Names
    are listable; values never leave the store except at resolve time.
  - `OAuthDeviceFlow`: RFC 8628 device authorization grant (start /
    poll / refresh) with injectable HTTP, so the whole flow tests
    offline against a fake provider.
  - `OAuthCredentialSource`: resolve with auto-refresh ahead of expiry
    (60s margin), refresh-token rotation per RFC 6749 §6, fail-closed
    on dead grants and refreshless expiry.
- `resolve_credential_with_source_fn` — resolution that also names the
  satisfying source; `SecretUsageEvent.metadata.source` now records it
  ('env', 'oauth_token_store', ...) so the audit trail says WHERE a
  credential came from, never what it was.
- Demo server: `POST /secrets/oauth/start` + `/secrets/oauth/poll`
  (owner-facing device-flow connect), token store registered at boot
  with refresh flows rebuilt from persisted provider config —
  connected accounts survive restarts. Token DB path:
  `ACTIVEGRAPH_TOKEN_DB` (default `data/activegraph_tokens.sqlite`).

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
