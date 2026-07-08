# Email Pack Changelog

## v0.1.2 — activegraph 1.3 compatibility (2026-07-08)

### Fixed
- `@tool` wrapper signatures satisfy the runtime's v1.3 registration-time
  validation: every parameter beyond the `(args, ctx)` invocation contract
  now has a default. No behavior change (behaviors call the `_fn`
  variants directly).

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
- 3 object types: `email_message`, `email_thread`, `email_draft`
- 4 relation types: `email_thread_contains`, `draft_responds_to`, `draft_from_candidate`, `email_linked_to_comm_thread`
- 3 behaviors:
  - `email_ingester` — on `email_message.created`: deduplicates by RFC 2822 Message-ID via `_EMAIL_MESSAGE_REGISTRY`; creates/updates `email_thread` (threaded by `In-Reply-To` / `thread_id`); creates `source(kind=email)` and `comm_message(channel=email, inbound)` for downstream packs
  - `reply_drafter` — on `comm_response_candidate.created (channel=email)`: reads original `comm_message` for sender address and subject; creates `email_draft` with `Re:` subject, optional signature, and correct `to_addrs`
  - `send_approver` — on `email_draft.created`: checks `to_addrs` against `owner_email_addresses` + `trusted_domains`; external sends create `action(kind=approval_request, risk_class=high)`; internal/trusted sends auto-approve and patch status to `"sent"`
- `EmailSettings` with `require_approval_for_external`, `owner_email_addresses`, `trusted_domains`, `auto_create_threads`, `draft_signature`, `max_body_length`, `default_reply_format`
- Tool functions: `ingest_email_fn`, `create_email_response_fn`
- Fixture scenarios: inbound email → source + CommMessage, external draft → approval gate, threaded reply deduplication
- Full README with approval gate flow diagram

### Design decisions
- SMTP/IMAP transport is out of scope — production integrations inject `email_message` objects via tool calls or webhooks; the pack owns structure, not transport
- `send_approver` cannot rely on patching `status="approved"` to re-trigger behaviors (patch_object does not fire `object.created`); internal auto-approve patches draft and candidate directly

### Known gotcha
- The Python standard library also has a module named `email`. If this pack's directory is on `sys.path` before the stdlib, `import email` may resolve to this pack instead of the stdlib. Always install the pack via `pip install -e ".[dev]"` (entry-point registration) rather than adding `packs/` to `sys.path` directly.
