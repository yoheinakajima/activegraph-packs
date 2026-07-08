# Bridges Pack Changelog

## v0.1.1 — Relation integrity fix (2026-07-08)

### Fixed
- **`add_relation` argument order.** Relation writes passed
  `(type, source, target)` but the API is `(source, target, type)` — the same
  bug the Chat Pack fixed in its v0.2.0. Affected relations were being written
  as garbage edges (the type string as the source id), silently breaking graph
  traversal over this pack's audit trail. Part of a repo-wide sweep (80 calls
  across 14 packs) that also corrected fixture assertions written against the
  broken shape (`r.source` where `r.type` was meant).

## v0.1.0 — Initial release (2026-06-03)

### Added
- `diligence_core_bridge` — maps Diligence pack objects to Core primitives without modifying the Diligence pack:
  - `document_to_source` — on `document.created`: creates `source(kind=document, content=summary)`; `derived_from(source → document)` relation
  - `claim_to_observation` — on `claim.created`: creates `observation(category=fact, confidence=claim.confidence)`; links to bridged source via `grounds` relation if source was already bridged; `derived_from(observation → claim)` relation
  - `memo_to_artifact` — on `memo.created`: assembles markdown content from summary, key claims, thesis questions, contradictions, and risks; creates `artifact(kind=memo, format=markdown, status=draft)`; `derived_from(artifact → memo)` relation
  - `risk_to_evaluation` — on `risk.created`: creates `evaluation(judgment=severity, rationale=title+description)`; `derived_from(evaluation → risk)` relation
- `_BRIDGE_SEEN` registry for deduplication (diligence_object_id → core_object_id)
- `DiligenceCoreBridgeSettings` (no configuration in v0.1)
- Fixture scenarios: all 4 bridge behaviors verified with Diligence + Core packs loaded together
- README documenting the bridge pattern, usage, and how to write a new bridge

### Design decisions
- Bridge packs subscribe to source-pack events and emit Core equivalents — they never modify the source pack
- `_BRIDGE_SEEN` prevents double-bridging if an event is replayed
- The `derived_from` relation type is owned by Core Pack; `vc_bundle.py` strips the Diligence pack's own `derived_from` declaration before loading so both packs can co-exist in one Runtime without a `PackConflictError`
- Bridge packs load after the source pack: `rt.load_pack(diligence)` then `rt.load_pack(diligence_core_bridge)`
