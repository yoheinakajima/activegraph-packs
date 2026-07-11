# Gmail connector pack

Gmail is the first connector-conformance case, not a special architecture.

```text
Composio Connect Link (route)
  → R0 profile + label probes
  → IntegrationProfile keyed (gmail, account)
  → Usage-owned communication surface
  → bounded message pages / Gmail history watermark
  → acquired item + replay artifact
  → provider-neutral evidence + shared annotations
```

The connector records canonical capabilities as `gmail.*`; `composio` is
route metadata. The Gmail pack has no hard dependency on the Composio pack:
capability registration accepts a route executor + operation map, and the
default registration lazily selects the Composio adapter. A later MCP or
native adapter can therefore keep the same canonical operations, profile, and
evidence semantics. Mailbox responses are content-addressed artifacts, not
copied into capability-result events. Backfill restarts use query overlap plus
the normalizer's stable evidence identity. Live polling uses Gmail `historyId`.

The same contract covers multiple accounts and route replacement. Profile
claims carry confidence, freshness, and probe provenance; owner corrections
supersede the profile. Unexpected shapes record a failure, mark claims stale,
and require a fresh structural exploration. Provider tombstones flow through
the normalizer-owned invalidation request, and OAuth revocation supersedes the
profile/surface without deleting retained evidence.

Authority is fixed: reads R0, a local draft is R1, creating a Gmail-hosted
draft is R2, and sending an existing draft is R3 forever. A send capability
can never become a standing scope. Draft effects also carry a stricter
per-call manual requirement while granted scopes remain unobservable.

Default backfill is `newer_than:30d`, 25 items/page, at most 250 messages and
10 pages. These are versioned settings and may be changed without altering the
connector contract.

The deterministic conformance suite covers pagination overlap, duplicate
delivery, interruption, partial bounds, rate limiting, invalid cursors,
unexpected shape/drift, tombstones, replay, multi-account identity, OAuth
revocation, and write idempotency. Polling ships first; push/webhook delivery
can later target the same history-watermark handoff.
