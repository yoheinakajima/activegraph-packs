# Changelog

## v0.2.0 — 2026-07-10

- Added `"manual"` to the `ConnectionPath` closed set (ADR 0025): the
  paste-back transport for assistant self-summaries. Purely additive;
  existing paths and evidence identity semantics are unchanged.

## v0.1.0 — Initial release

- Added strict acquired-item/content contracts and provider-neutral evidence identity.
- Added immutable replay modes, revisions/supersession, stable cursors, and failure records.
- Added versioned deterministic extraction with typed candidate provenance and invalidation.
