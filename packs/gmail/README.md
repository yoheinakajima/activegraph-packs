# Gmail connector pack

Gmail is the first connector-conformance case, not a special architecture.

```text
Composio Connect Link (route)
  → R0 profile + label probes
  → IntegrationProfile keyed (gmail, account)
  → Usage-owned communication surface
  → bounded message pages / Gmail history watermark
  → acquired item + replay artifact
  → provider-neutral evidence
  → Gmail mapper → conversation family + exact-span optional interpretation
```

The connector records canonical capabilities as `gmail.*`; `composio` is
route metadata. The Gmail pack has no hard dependency on the Composio pack:
capability registration accepts a route executor + operation map, and the
default registration lazily selects the Composio adapter. A later MCP or
native adapter can therefore keep the same canonical operations, profile, and
evidence semantics. Mailbox responses are content-addressed artifacts, not
copied into capability-result events. Backfill restarts use query overlap plus
the normalizer's stable evidence identity. Live polling uses Gmail `historyId`.

The default Composio route resolves only the Gmail pack's explicit operation
candidates against the live provider catalog. `latest` is converted once per
process into a concrete available toolkit version and input-schema fingerprint;
that hardened selection is recorded in every capability receipt. Explicit bad
pins and missing/deprecated candidates fail before execution instead of leaking
through as ambiguous provider 404s.

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
connector contract. Reaching the cap is a successful bounded sample expressed
as `partial`, not a failed import.

Communication is multi-subject and high-volume. Gmail therefore maps headers,
threads, labels, and current body text into the neutral conversation family
before any optional model interpretation. Quoted history, signatures,
boilerplate, tracking, notifications, and injection-shaped content are removed
from model eligibility deterministically; selected spans remain exact evidence
selectors. In particular, email addresses, URLs, preferences, and assertions in
mail cannot become owner profile candidates.
Profile projection requires evidence metadata explicitly scoped as
`subject_scope=owner_profile`. Dedicated Gmail/entity/task projectors may add
richer semantics later without weakening this subject boundary.

`reprocess_gmail_evidence` applies the current mapper/filter/profile to retained
evidence with an explicit predecessor link and no provider call. A terminal
batch is materialized once; optional model upgrades are capped by the active
connector operational policy (10 provider calls in v1), with the remainder
recorded as deterministic/deferred rather than silently skipped.

The deterministic conformance suite covers pagination overlap, duplicate
delivery, interruption, partial bounds, rate limiting, invalid cursors,
unexpected shape/drift, tombstones, replay, multi-account identity, OAuth
revocation, and write idempotency. Polling ships first; push/webhook delivery
can later target the same history-watermark handoff.

After one bounded backfill, the neutral connector maintenance tool can request
a manual refresh. Gmail alone resolves the stored `history:<id>` watermark and
proposes `gmail.history.list`; clients never parse Gmail cursor fields. A
successful poll with no watermark advance does not disable future checks: each
later owner request receives a fresh bounded run, while concurrent work and a
rate-limited retry remain idempotent.

Gmail history is a change feed, not a transaction with message hydration. A
message can be listed and then disappear before `messages.get`. A message-level
404 is therefore settled as a concurrent provider deletion: the pack records a
tombstone, never counts the message as imported, and advances the watermark only
after every listed id is either imported or tombstoned. This rule is independent
of result arrival order. A history-list 404 remains a distinct invalid-cursor
failure that requires re-anchoring.

Sent-mail comprehension (ADR 0045) is a declared recipe, not a Gmail-grown
reduction engine: Gmail owns the consent plan and the eligible-item selection;
the staged reduction is `subject_synthesis.comprehension` machinery. The plan
(`purpose=comprehension`, recipe `gmail_sent_v1`) reads the latest-N messages
the owner sent — canonical Sent semantics via the `in:sent` search scope,
never a UI label string, with the latest-N bound coming from the plan caps
rather than a date term — and discloses the provider and fast model that will
summarize. The count is an editable cap; decline, a smaller count, and later
execution are first-class outcomes. Selection runs over the materialized
conversation family, so deterministic hygiene has already stripped quoted
history, forwarded bodies, and signatures: only owner-authored outbound text
qualifies; drafts, automated outbound, injection-held, and
empty-after-normalization items are excluded with recorded exclusion counts
and coverage; recipients appear as identity/domain only; originals stay local
as replay artifacts and every summary row cites its message evidence.
