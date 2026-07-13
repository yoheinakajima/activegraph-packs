# Changelog

## 0.4.0 — attribute classes (2026-07-13)

- ADR 0043: subject attributes carry a class — identity, instruction, or
  narrative (the unlisted default). `attribute_classes` settings plus the
  `classify_subject_attribute` helper and `DEFAULT_ATTRIBUTE_CLASSES` for
  projectors without a settings instance. Identity headlines recognition;
  instructions belong in the behavior surface; narrative folds.

## 0.3.0 — promotion is idempotent and multi-valued (2026-07-13)

- Promotion is idempotent by value: re-confirming a value the subject
  already holds resolves the verdict to the existing fact instead of
  minting a near-duplicate (ADR 0042 / D062).
- Contradictions apply only to declared single-valued attributes
  (`single_valued_attributes`, default `["name"]`); every other attribute
  accumulates — a second confirmed handle, url, project, or company is
  more identity, not a conflict.
- `review_subject_fact_fn` accepts host `metadata`; the verdict carries it
  and the applied fact inherits it (e.g. marking self-declared seeds).

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

