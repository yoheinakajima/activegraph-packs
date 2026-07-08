# Memory Gateway Pack Changelog

## v0.2.0 — Curation: the judgment layer (2026-07-08)

### Added
- **Provenance admission** (`provenance_admission`, default 'trusted_senders'):
  the evaluator — the lifecycle's governance point — now decides WHOSE words
  become memory. Conversations build memory (chat_message sources; the reply
  gate already governs who converses and the memory is subject-scoped to the
  speaker), but guidance categories (instruction/preference/decision)
  extracted from non-conversational content (emails, documents, tool
  results) are rejected unless the sender resolves to a trusted principal.
  Documents don't give orders. Rejections carry a written rationale;
  enforced only when identity verification is possible (same graceful
  degradation as the gateway's approver check).
- **Frame-scoped recall guard**: memory items record the frame they were
  born in; `retrieve_by_query(..., exclude_frame_id=...)` (plumbed through
  `retrieve_memories_fn` and used by chat_memory_context) guarantees recall
  never returns a memory created by the very turn that is asking — what
  used to be a timing accident is now a designed invariant.
- `auto_accept_min_confidence` (default 0.5).

### Fixed
- **`auto_accept_categories` now does something.** It was documented as
  category-based auto-accept but only decorated the rationale string.
  Priority categories now accept at the relieved
  `auto_accept_min_confidence` bar instead of the full
  `acceptance_threshold` — priority relieves the threshold, it does not
  suspend judgment.

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
