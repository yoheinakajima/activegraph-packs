# Connector Control Pack Changelog

## v0.6.0 — durable external work (2026-07-13)

- Add the durable `external_work_attempt` ledger (ADR 0041 as amended by
  ADR 0045): every externally-performing work unit records crash-safe
  prepared/performing/commit_pending/terminal phases under an idempotency
  key. A crash between perform and commit can neither duplicate the external
  call nor lose its result — restart finds the persisted outcome and commits
  it, retries under the explicit attempt policy, or surfaces blocked/failed
  state. Payloads and outcomes are bounded, secret-scanned JSON; oversized
  material blocks loudly instead of truncating silently.
- Project the ledger operationally (`project_external_work_attempts_fn`):
  every failure and retry is visible, never a silent one;
  `pending_commit_attempts_fn` is the restart worklist.
- Add the `comprehension` ingestion-plan purpose beside `initial_backfill`
  and `extension` for ADR 0045 comprehension consent plans.
- Supersede `connector-operational@0.2.0` with `@0.3.0`: raise
  `max_provider_calls` to 16 so an ADR 0045 campaign (a 12-query research
  run or a batched sent-mail reduction) stays inside one run's call budget.

## v0.5.0 — deferred plan execution seam (2026-07-13)

- A service may register the ADR 0041 three-phase seam
  (prepare/perform/commit) beside its synchronous plan executor; begin and
  commit carry the exact execution gates of the synchronous path and fail
  closed on mid-flight supersession. `pending_deferred_plan_executions_fn`
  is the host pump's poll. Approval remains the only trigger.

## v0.4.0 — receipted ingestion plans (2026-07-12)

- Add the versioned `connector_ingestion_plan` artifact (ADR 0039 / D059):
  service-derived proposals with window derivation, per-surface expectations,
  policy-sourced caps, and interpretation stages; owner edits supersede
  (ADR 0020) and record verdict evidence against the acceptance prediction the
  proposal recorded before any verdict existed (ADR 0018).
- Enforce acquisition ceilings from the operational policy: lowering plan
  bounds is free; raising one past `max_acquisition_items/pages` fails with an
  explicit policy-escalation message.
- Bind acquisition runs to the exact approved plan version through a
  service-registered executor; superseded plans can never execute, and a
  neutral behavior settles executing plans when their bound run turns
  terminal.
- Supersede `connector-operational@0.1.0` with `@0.2.0`, naming the
  acquisition ceilings; report planned-vs-actual through the learning delta's
  new `plan` field. Control contract `connector_control@0.2.0` (additive).

## v0.3.0 — maintenance requests and lifecycle truth (2026-07-12)

- Add graph-visible provider-neutral manual refresh requests dispatched through
  service-owned handlers, with active-work dedup and fail-closed revoke gates.
- Project source lifecycle changes into connector binding authority state.

## v0.2.0 — conversation visibility (2026-07-12)

- Extended the family-neutral conversation summary with a bounded latest-message
  preview, sender, interpretation state, and message drill-down reference.

## v0.1.0 — 2026-07-12

- Added neutral surface bindings, domain-run observations, learning deltas,
  and five validated connector-family native read contracts.
- Added the versioned `connector-operational@0.1.0` release policy and
  non-persisted conformance measurement for the recorded 250-item fixture.
