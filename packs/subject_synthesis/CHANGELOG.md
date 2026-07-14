# Changelog

## 0.3.0 — the governed agentic loop (2026-07-14)

- Understanding affordances (ADR 0047 §2): the typed registry by which any
  source joins a comprehension campaign — teaches, capabilities/scopes,
  schemas, privacy/outward-disclosure, reductions, drill-down permission
  with bounds and a source-owned selector, budgets, destinations, and
  coverage requirements. Validation runs at registration.
- Source lenses and one versioned working understanding (ADR 0047 §3–4):
  every contribution separates `support_refs` from `context_refs`; borrowed
  context never corroborates; entries carry authority classes
  (owner_confirmed / hypothesis / unresolved / denied); material changes
  schedule targeted, version-pinned reinterpretation — never a global rerun.
- The dynamic coordinator (ADR 0047 §1, §5): typed move grammar
  (inspect_source / outward_query / reduce_fast / drill_down /
  align_entities / ask_owner / propose_amendment / synthesize / stop), a
  deterministic host validator over consent, plan versions, scope, action
  class, privacy authority, provider support, budgets, and owner-decision
  boundaries; a zero-key deterministic proposer preserving today's
  lifecycle; the reasoning-role model proposer on the prepare/perform/commit
  seam; owner questions that pause and resume the campaign; and bounded,
  fully-recorded evidence drill-downs whose uncited findings are dropped.
- Three logical model roles (ADR 0047 §6) recorded on every call:
  reasoning/coordinator, balanced (aggregation moved here from fast), fast.

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
