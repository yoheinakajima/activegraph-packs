# Changelog

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
