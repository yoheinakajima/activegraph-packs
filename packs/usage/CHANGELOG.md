# Changelog

## v0.3.0 — 2026-07-10

- Added the provider-neutral `source_connection_request` handoff so service
  connectors can request a source while Usage remains the only owner of
  canonical connection surfaces and lifecycle events.
- Added provider-stable cursor watermarks for polling connectors.

## v0.2.0 — 2026-07-10

- Added `"manual"` to the connection-path closed set (ADR 0025): the
  paste-back transport. Additive; not a live path (paste-back is
  snapshot-shaped), so `LIVE_CONNECTION_PATHS` is unchanged.

## v0.1.0 — Initial release

- Added closed source-category validation and provider-neutral connection surfaces.
- Added named/versioned volume-or-provider-time settlement gates.
- Added explicit-horizon coverage, interaction, and outcome projections.
- Added deterministic lifecycle replay, fixture exclusion, and source-zero dogfood fixtures.
