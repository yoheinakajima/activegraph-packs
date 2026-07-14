# Changelog

## 0.2.0 — staged comprehension and the setup draft (2026-07-13)

- Connector comprehension recipes (ADR 0045 §3–4): a connector participates
  in comprehension by registering a declared recipe (selection rule,
  privacy exclusions, leaf-schema subset, aggregation grouping, budgets,
  destinations) — never by growing its own reduction engine. The neutral
  leaf row schema is fixed here.
- Staged reduction: eligible items → batched fast-model leaf rows (one
  structured row per item; evidence refs come from the payload by
  construction) → bounded aggregation by group when volume exceeds the
  reasoning budget. Coverage is recorded at every stage — silent truncation
  is a defect by definition; model output is sanitized and
  injection-flagged; every call records its resolved provider/model and a
  response breadcrumb. A commit replayed after a crash cannot double-append
  leaf rows or aggregates. Leaves may summarize and extract; they may never
  promote.
- The setup draft (ADR 0046 / D068): one versioned, editable proposal of
  the owner's world from bounded provenance-bearing inputs (promoted facts,
  research findings, comprehension summaries, signal maps — deterministic
  packing, included refs recorded), with six routed sections (identity,
  narrative, instructions, projects, people, access). No uncited item may
  commit; every item records its prediction before any verdict exists; the
  review grammar is accept/reject/edit/merge/reclassify/defer, and an owner
  edit supersedes without minting a prediction win. Staged submission
  promotes through the canonical pipelines (subject_profile verdicts,
  project confirms, access hints) with restartable partial failure; the
  zero-key deterministic composer reaches the identical review gate.
- New `information_access_hint` object: an accepted access strategy is
  working knowledge about where information lives — never memory, never
  identity.

## 0.1.0 — determinism floors, synthesis proposes, verdicts promote (2026-07-13)

- New pack (ADR 0043 / D064): a bounded, provider-gated comprehension pass
  over promoted subject facts (classed identity/narrative/instruction),
  the owner's connector taxonomy, and recurring entities.
- Proposes structured identity `profile_candidate`s anchored to
  owner-scoped evidence and curated `project_candidate`s (kind
  `synthesized`) — every proposal cites refs from the prepared input or is
  dropped at commit; verdicts remain the only promotion.
- Durable `subject_synthesis_request` work unit + `subject_synthesis_run`
  receipt (inputs, proposals, deliberate noise, bounded response sample).
- Three-phase seam mirrors semantic_extraction's deferred shape (ADR
  0041): prepare/perform/commit for host pumps, with a synchronous
  composition as the pack default.
