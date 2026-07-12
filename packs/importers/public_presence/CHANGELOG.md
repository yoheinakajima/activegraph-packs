# Changelog — public_presence

## 0.2.0 — 2026-07-12

- Mark public evidence `unverified_public` and `review_required`; fetched page
  text may propose findings but can no longer auto-admit memory.

## 0.1.0 — 2026-07-10

- `public_presence.fetch_page`: zero-key R0 gateway capability (stdlib
  fetch + stdlib HTML→text).
- `bootstrap_public_presence`: deterministic handle→URL planning, hard
  per-run budget with logged overflow, `presence_bootstrap_run` ledger.
- `acquire_presence_result`: results → injection-scanned evidence with
  artifact-mode replay payloads on the `public_presence` surface.
- Keyed Firecrawl-grade upgrade seam via settings (provider/capability
  selection), suggested never required.
