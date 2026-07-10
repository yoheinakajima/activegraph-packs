# Skills Pack Changelog

## v0.2.0 — Replay verification for promoted versions (P6) (2026-07-10)

### Added
- `verification.verify_skill_replay_fn`: a promoted skill version earns
  `replay.verified` keyed `(subject_id=skill_id,
  subject_version=version)` when its recorded trial re-runs inside a
  runtime FORK (fork-trial machinery; requires a SQLite-backed runtime)
  and reproduces the recorded governed shape, after definition-hash
  integrity and ADR 0015 lineage checks. No recorded usage, a diverging
  re-run, or `reference_only` source lineage fails LOUDLY
  (`SkillReplayIncompleteError`); the fork is discarded and only the
  verification event lands on the real graph; emits once per version.

## v0.1.0 — Governed learned artifacts (2026-07-09)

- Added immutable semantic versions with source-evidence provenance.
- Added idempotent exact-version usage and evaluation links.
- Added evidence-gated promotion, reversible demotion, and durable history.
- Added capability-call routing for declared external effects.
- Added reliability-driven eligibility hooks, separate from all score surfaces.
