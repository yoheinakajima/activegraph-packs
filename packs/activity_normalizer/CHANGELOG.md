# Changelog

## Unreleased — ADR 0026 steps 2-3: onto the shared extraction layer

- `activity.structure@0.2.0`: the legacy structure heuristics re-homed as
  an annotation emitter at the semantic_extraction seam. Emits the same
  findings as source-anchored annotations under namespaced facets
  (`activity.memory`/`preference`/`task`/`profile`/`skill`/`eval`).
- `select_shared_extraction` behavior: mints the next extraction_profile
  version routing those facets onto the shared layer when the profile is
  seeded (curated shared-path selection, D041).
- `project_structure_candidates` behavior: compatibility candidate
  projectors minting the legacy candidate types from `activity.*`
  annotations, keyed by the legacy candidate identity for cross-boundary
  idempotency.
- The direct evidence→candidate write path (`run_extraction` from
  `normalize_acquired_item`) is disabled by default; `legacy_extraction_enabled`
  re-enables it for rollback. New settings: `legacy_extraction_enabled`,
  `select_shared_extraction`, `compat_candidate_projectors`.

## v0.2.0 — 2026-07-10

- Added `"manual"` to the `ConnectionPath` closed set (ADR 0025): the
  paste-back transport for assistant self-summaries. Purely additive;
  existing paths and evidence identity semantics are unchanged.

## v0.1.0 — Initial release

- Added strict acquired-item/content contracts and provider-neutral evidence identity.
- Added immutable replay modes, revisions/supersession, stable cursors, and failure records.
- Added versioned deterministic extraction with typed candidate provenance and invalidation.
