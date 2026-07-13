# Changelog

## 0.3.0 — 2026-07-12

- Add the neutral `attention.signal_observed` intake: family/domain packs may
  emit semantic signals, while attention alone validates absence censorship
  and owns vector state.
- Consume outbound conversation replies as thread/person importance without
  creating a Gmail-specific ranker.

## 0.2.0 — 2026-07-12

- Add typed context-scoped `importance_vector` and domain-scoped
  `source_trust_vector` projections with complete evidence references.
- Learn importance from semantic engagement/explicit outcomes and trust only
  from canonical source-attributed outcomes; LLM judgments and source
  self-description have zero direct weight.
- Add explain queries and deterministic ranking with an explicit exploration
  reserve. Unknown remains unranked and unknown trust remains neutral.

## 0.1.0 — 2026-07-10

- Added semantic attention observations and bounded interaction batches.
- Deferred importance/trust model representation and weights to the dedicated
  ADR 0028 vectors round; v0.1 is observation infrastructure only.
