# Changelog

## 0.2.0 — owner anchoring (2026-07-12)

- Add the owner alias-set projection (`owner_alias_set_fn`): a deterministic,
  horizon-stable read over promoted `subject_fact`s — confirmed addresses,
  handles, and url-derived domains, following supersession; bootstrap-origin
  confirmations qualify identically (ADR 0039).
- Seed importance from confirmed relationship/company facts as explicit owner
  acts: `attention.signal_observed` with `explicit_important`, aligned with
  the conversation adapter's opaque person refs. Identity aliases never seed
  importance, and trust stays strictly outcome-only — no `outcome.*` event is
  ever emitted here (ADR 0038).

## 0.1.0

- Add explicit candidate review, promoted subject facts, contradictions, and
  forget-as-supersession.

