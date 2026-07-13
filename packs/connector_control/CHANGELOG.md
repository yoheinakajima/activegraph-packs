# Connector Control Pack Changelog

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
