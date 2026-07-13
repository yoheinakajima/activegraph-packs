# Changelog — semantic_extraction

## v0.3.0 — guarded proceduralization pilot (2026-07-12)

- Add the first concrete ADR 0029 lifecycle: reviewed LLM reference witnesses
  -> declarative deterministic parser candidate -> real runtime fork over
  witnessed/held-out/counterexample sets -> fail-closed evaluation promotion ->
  named approval -> guarded execution.
- The guard is part of the artifact, uses a calibrated communication shape plus
  explicit-request cue, and requires zero false admissions and exact held-out
  selector parity. A counterexample false admission blocks promotion.
- Guard abstention/shape drift records `procedure_deoptimization`, invokes the
  exact LLM reference fallback, and demotes after three distinct shape drifts.
  Candidate execution makes zero provider calls and cannot widen R0 effects.

## v0.2.0 — exact-span domain requests (2026-07-12)

- Added `selection_extraction_request`, allowing domain/family projectors to
  select exact authoritative evidence spans while this pack retains ownership
  of extractor resolution, caching, provider calls, and settlement.
- Added selection identity to extraction cache identity and coverage. Every
  returned selector is offset back into the original evidence and hash-checked
  before execution.
- Evidence marked with an interpretation family bypasses whole-document eager
  extraction so family hygiene cannot be accidentally circumvented.

## Unreleased — D025 stage two: the LLM-backed extractor

- Candidate eligibility is copied from evidence into the annotation envelope
  so registry filtering skips ineligible projectors before invocation.
  Multi-subject communication cannot mint profile or memory candidates merely
  because an email contains an assertion; family interpretation must first
  resolve subject and meaning.

- `semantic.llm@0.1.0` registered beside `semantic.deterministic@0.1.0`:
  same annotation contract, same cache identity scheme, different
  extractor id. Implements `entity_mention`, `assertion` (with
  modality/polarity judgment), `preference_expression`, and the two
  facets the floor does not: `relation_mention`, `event_mention`.
- Recorded provider seam: with `llm_record_dir` set, provider calls
  replay from the prompt-hash-keyed record first (the runtime's own
  fixture format); re-extraction never re-contacts, and a rebuild
  reproduces byte-equal annotations keylessly.
- Selector verification: LLM-proposed spans are checked byte-for-byte
  against the content; non-matching spans are dropped.
- `extraction_profile.extractor_by_facet`: the profile now decides which
  extractor serves which facet. Provider configured → the seeded default
  routes `relation_mention`/`event_mention` to `semantic.llm` (floor
  stands, D041); no provider → profile unchanged, byte-identical
  behavior.
- Fork-trial-promote (ADR 0014): `semantic.llm` lands as a `candidate`
  extractor state; `run_extractor_trial` records the per-facet
  deterministic-vs-LLM comparison as `extractor_promotion_evidence`;
  `promote_llm_extractor` re-routes facets only with evidence + a named
  approver.
- Typed bodies for `relation_mention` and `event_mention`;
  `entity_mention.kind` widened with semantic kinds (person,
  organization, place, product, other).
- Committed LLM records under `fixtures/llm_records/` (seeded via
  `fixtures/seed_llm_records.py`; re-record live with
  `ACTIVEGRAPH_SEED_LIVE=1`); fixture [5] exercises the upgrade, trial,
  and promotion keylessly.

## 0.1.0 — 2026-07-10

First implementation of the shared annotation layer (ADR 0026, slice 5a).

- `semantic_annotation` envelope over the ten standard facets; typed
  bodies for the six facets the deterministic v1 extractor implements.
- `semantic.deterministic@0.1.0`: byte-deterministic zero-key extractor
  (entities, assertions, questions, preferences, temporal expressions,
  topic tags) with a registry seam for LLM-upgraded extractors.
- Cache identity `(evidence_revision, extractor_id, extractor_version,
  config_hash, requested_facets)`; facet-incremental re-extraction.
- First-class `extraction_coverage` records + `annotation_coverage` tool.
- `extraction_profile` versioned config artifact (D042) seeded with the
  D041 eager floor; `update_extraction_profile` supersession tool.
- Profile- and memory-candidate projectors over annotations
  (`projected_from_annotation` provenance).
- `invalidate_annotation_extractor`: version demotion through provenance
  with evidence intact.
