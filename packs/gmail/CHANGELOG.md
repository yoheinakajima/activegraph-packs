# Changelog

## 0.1.0 — 2026-07-10

- Add budgeted exploration, canonical service/account profiles, bounded Gmail
  backfill, history-watermark polling, replay artifacts, and conservative
  effect classifications.
- Add explicit local R1 draft → held R2 provider draft → held R3 send
  transitions with client idempotency guards; sending never auto-runs.
- Add multi-account product status, claim provenance/correction, bounded-partial
  semantics, rate-limit retry, invalid-cursor re-anchor, shape drift and forced
  re-exploration, provider tombstones, and OAuth revocation without erasure.
- Adapt authoritative Gmail runs into the neutral connector control plane.
  Learning now settles once when every imported evidence item has extraction
  coverage instead of rescanning and rewriting the aggregate per annotation.
