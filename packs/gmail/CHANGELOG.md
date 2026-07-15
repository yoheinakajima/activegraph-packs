# Changelog

## 0.6.0 — the sent-mail understanding affordance (2026-07-14)

- Raise the sent-recipe `max_tokens_per_call` budget to 4,000: the owner's
  live keyed run lost 3 of 5 leaf batches to response truncation at the
  2,000-token ceiling (fenced JSON cut mid-stream parses to zero rows and
  lands in coverage as "model_skipped"). Recording truncation explicitly
  and retrying smaller batches remains a follow-up.

- Declare `gmail_sent_understanding` (ADR 0047 §2): the typed affordance by
  which sent mail joins a governed comprehension campaign — teachable
  signals, `messages.fetch` capability with the `in:sent` scope, privacy and
  provider-only disclosure rules, the `gmail_sent_v1` recipe as its
  reduction, and a bounded drill-down selector
  (`select_sent_drill_down_excerpts`) over the hygiene-clean authored view.
  Acquisition semantics stay in this pack; the coordinator discovers the
  source through the declaration, never through a bespoke wizard branch.

## 0.5.0 — sent-mail comprehension (2026-07-13)

- Declare the `gmail_sent_v1` comprehension recipe (ADR 0045): Gmail owns
  exactly what is service-specific — the consent plan and the eligible-item
  selection — while the staged reduction itself is subject_synthesis
  machinery.
- The consent plan (`purpose=comprehension`) reads the latest-N messages the
  owner SENT: canonical Sent semantics via the `in:sent` search scope, never
  a UI label string, with the latest-N bound coming from the plan caps
  rather than a date term. The plan disclosure names the provider and fast
  model that will summarize, and the count is an editable cap — decline, a
  smaller count, and later execution are first-class outcomes.
- Selection runs over the materialized conversation family, so deterministic
  hygiene has already stripped quoted history, forwarded bodies, and
  signatures: only owner-authored outbound text qualifies, drafts /
  automated outbound / injection-held / empty-after-normalization items are
  excluded with recorded exclusion counts and coverage, recipients appear as
  identity/domain only, and originals stay local as replay artifacts with
  every summary row citing its message evidence.

## 0.4.0 — comprehension spent: plans and measured signal maps (2026-07-12)

- Exploration ends with a service-derived ingestion-plan proposal — the first
  consumer of the recorded `data_topology` (ADR 0039). An optional third R0
  probe samples the newest message ids+dates (no payloads) so the window
  derivation can cite a measured recent-activity rate; without it the plan
  says exactly what stayed unmeasured.
- `request_gmail_backfill` now executes the current approved plan version:
  window, caps, and page size come from the plan, superseded plans can never
  execute, and a backfill without an approved plan fails loud. Owner label
  exclusions render into the Gmail query when the label name allows it.
- Replace the hardcoded signal map: inbox richness derives from measured
  volume/rate with measurement provenance, per-label surfaces are explicitly
  `unmeasured`, and the `signal.inbox_richness` claim mirrors the measured
  value. Learning deltas report planned-vs-actual for plan-bound runs.

## 0.3.0 — continuous manual maintenance (2026-07-12)

- Register Gmail behind the neutral maintenance contract and poll from the
  durable history watermark with bounded work receipts.
- Repeated no-advance polls create fresh runs while active/rate-limited retries
  remain idempotent; expose namespaced thread labels beside family unread data.

## 0.2.0 — conversation-family mapping (2026-07-12)

- Map recorded Gmail evidence into strict service-neutral conversation threads,
  messages, participants, entity mentions, and staged interpretation runs.
- Preserve Gmail headers needed for service semantics while keeping provider
  payload parsing inside this pack. Notifications and injection-shaped content
  remain displayable but are never model eligible.
- Materialize once at a terminal batch boundary, publish a ready native view,
  and cap optional model upgrades through the graph operational policy.
- Add bounded local reprocessing over replay evidence with explicit lineage and
  a hard guarantee that Gmail is not contacted.

## 0.1.0 — 2026-07-10

- Add budgeted exploration, canonical service/account profiles, bounded Gmail
  backfill, history-watermark polling, replay artifacts, and conservative
  effect classifications.
- Add explicit local R1 draft → held R2 provider draft → held R3 send
  transitions with client idempotency guards; sending never auto-runs.
- Add multi-account product status, claim provenance/correction, bounded-partial
  semantics, rate-limit retry, invalid-cursor re-anchor, shape drift and forced
  re-exploration, provider tombstones, and OAuth revocation without erasure.
- Adapt authoritative Gmail runs into the neutral connector control plane.
  Learning now settles once when every imported evidence item has extraction
  coverage instead of rescanning and rewriting the aggregate per annotation.
