# Changelog

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
