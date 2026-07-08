# Memory Gateway Pack Changelog

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
- 3 object types: `memory_item`, `memory_retrieval`, `memory_ranking`
- 3 relation types: `accepted_as`, `ranked_in`, `scored_by`
- 3 behaviors:
  - `candidate_evaluator` — accepts/rejects memory_candidates from Core
  - `memory_writer` — promotes accepted candidates to MemoryItems
  - `memory_ranker` — scores retrieval results by keyword overlap
- `retrieve_memories` tool with top_k, min_score, category filtering
- `SqliteMemoryBackend` with in-memory and file-based SQLite support
- LRU eviction when max_items exceeded
- `MemoryGatewaySettings` with acceptance_threshold, max_items, backend_url
- Fixture: memory_lifecycle (candidate → evaluation → item → retrieval → ranking)
- Full README with behavior map

### Design decisions
- Default backend is in-memory SQLite (no deps, works everywhere, no persistence)
- candidate_evaluator fires on all memory_candidate.created (not filtered by category)
- auto_accept_categories provides fast-path for preference/instruction/decision
- Ranking uses Jaccard keyword overlap (same as Core task_linker) — LLM rerankers for v0.2
- Backend singleton per db_url — in-memory backends shared within a process run
