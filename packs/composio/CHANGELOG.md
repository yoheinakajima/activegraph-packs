# Changelog

## 0.1.1 — 2026-07-16

- `peek_redirect`: a non-consuming claimability check for the one-shot
  Connect Link side channel, so hosts can serve a READ-ONLY link-status
  endpoint while the mutating begin command is issued exactly once per
  owner click and the redirect is claimed exactly once (ADR 0051 §6).


## 0.1.0 — 2026-07-10

- Add current hosted Connect Link flow and service-scoped status checks.
- Keep redirect URLs out of the graph log.
