# Memory Gateway Pack Changelog

## Unreleased

- Treat SQLite `file:` backend URLs as URIs so shared in-memory test stores do
  not leak literal `file:mem_*?mode=memory&cache=shared` files into worktrees.

## v0.9.0 — Learned source-trust arbitration (2026-07-12)

- Weight competing evidence sources with domain/query-scoped trust vectors
  while preserving the evidence-authoritative floor.
- Record raw relevance, weighted relevance, trust vector, verdict, and outcome
  references in every query resolution. Unproven trust is neutral.

## v0.8.0 — Evidence-authoritative resolution and source admission (2026-07-12)

- Add a graph-visible procedure resolver spanning raw evidence, annotations,
  admitted items, and the reserved live-lookup slot. Evidence is packaged
  first and remains authoritative over derived artifacts.
- Replace “missing sender means internal” with explicit evidence admission
  posture. Unverified public content now requires corroboration/owner review;
  injection-flagged evidence is rejected.

## v0.7.0 — Versioned promotion beyond admission (P6) (2026-07-10)

### Added
- `memory_item` gains `promotion_status` (admitted/promoted/demoted),
  `artifact_version` (fixed at admission; new text = new item, never an
  in-place bump), and `promotion_history`.
- `memory_promotion_proposal` object type +
  `memory_promotion_proposer` behavior: reliability evidence GENERATES
  proposals (versioned rule `memory.promotion.reliability@1`: supported
  verdict + >= 2 helped outcomes -> promote candidate; harmful/stale on
  a promoted version -> demote candidate). Proposals carry the exact
  reliability evidence event ids.
- `promotion.resolve_memory_promotion_fn`: the ONLY promote/demote path
  — explicit approver required; approval emits `memory.promoted` keyed
  `(artifact_id, artifact_version)` (the SCORING_CONTRACT identity, so
  re-promotion of the same version can never re-score) or
  `memory.demoted`. Nothing promotes silently.
- `promotion.verify_memory_replay_fn`: `replay.verified` for promoted
  versions keyed `(subject_id, subject_version)` from recorded checks
  (admission re-derivation + stored-artifact retrieval through the
  recorded embedding path). Fails LOUDLY on `reference_only` /
  replay-incomplete source lineage (ADR 0015) and on failed checks;
  emits once per version.

## v0.6.0 — P10: first-party embedding rides the recorded runtime path (2026-07-10)

### Changed
- memory_writer, memory_retriever, and retrieve_memories_fn (new optional
  `ctx=` parameter) bind the runtime's RECORDED embedding path
  (`ctx.embed` / `Runtime.embed`, runtime CONTRACT v1.8 #6) around their
  backend calls whenever the runtime has an embedding_provider: every
  first-party embed now emits embedding.requested/responded events and
  replays from the log with zero provider contact
  (`Runtime.load(..., replay_embedding_cache=True)`). When a recorded
  path is bound, a failing embed degrades to lexical — it never silently
  falls back to an unrecorded direct call.
- Direct provider calls (`set_embedder(...)` without a runtime provider)
  remain fully supported for third-party embedders and bare-graph hosts,
  but are no longer used by first-party packs when a runtime records
  embeddings. Hosts migrate by passing the same embedder object to
  `Runtime(embedding_provider=...)` — both pack embedders already
  implement the runtime protocol.

### Added
- backend.runtime_recorded_embedding(handle): context manager binding a
  behavior ctx (or Runtime) as the preferred embedding path; task-local
  and nesting-safe.
- Fixture `recorded_embedding_replay`: a stored memory + retrieval
  round-trip is recorded, then the same retrieval replays against a
  raise-on-contact provider and is served entirely from the log.

## v0.5.0 — Outcome-aware forgetting hooks (2026-07-09)

### Added
- `memory_reliability_applier` consumes graph-visible reliability changes and
  applies reversible retrieval multipliers to memory items and backends.
- Retrieval objects and rankings expose raw relevance, the artifact
  reliability verdict, its multiplier, and adjusted relevance.
- SQLite and the external-backend seam share the same reliability hook; the
  mem0 adapter applies it after provider retrieval.

## v0.4.0 — Runtime EmbeddingProvider seam adoption (2026-07-08)

### Changed
- `OpenAIEmbedder` and `HashEmbedder` now implement the runtime's
  `EmbeddingProvider` protocol (`embed(*, texts, model)` +
  `default_model`, activegraph >=1.3), so they drop into
  `Runtime(embedding_provider=...)` unchanged. BREAKING for subclasses:
  `embed` is keyword-only now.
- The backend seam accepts BOTH shapes: runtime providers and the
  legacy `embed(texts)` Embedder protocol (still public API), via
  `backend._invoke_embedder`. The runtime's own `HashEmbeddingProvider`
  works behind `set_embedder()` unmodified (proof test in
  `tests/test_memory_embedding_seam.py`). Retrieval logic stays in this
  pack, as agreed with the runtime.

## v0.3.0 — Retrieval quality + pluggable backends (2026-07-08)

Fixes the July 2026 agent-readiness report §5.1 (verified recall failures:
"teal", "bakery", and rephrased questions all missed a stored memory at the
default threshold) and opens the store seam to external memory services.

### Fixed
- **Lexical recall brittleness.** Scoring is now
  `max(Jaccard overlap, query-term coverage)` (`backend.lexical_score`).
  Coverage is immune to stored-sentence length, so short/keyword queries and
  natural questions recall; interrogative words are stopworded so questions
  don't dilute their own coverage. Every §5.1 failure case is a regression
  test (`tests/test_memory_retrieval_quality.py`).
- **Embedding mode no longer replaces the lexical signal.** With a vector
  present, an item's score is `max(cosine, lexical)` — a memory is as
  relevant as its strongest signal. Enabling embeddings can never lose an
  exact-keyword hit; strictly never worse than either pure mode.
- `memory_ranker` uses the shared `lexical_score` instead of a private
  (differently-stopworded) Jaccard.

### Added
- **`MemoryBackend` protocol + scheme registry.** `register_backend("mem0",
  factory)` routes any `mem0://…` backend_url through the factory —
  one settings value switches the entire lifecycle to an external store.
  `ExternalMemoryBackend` base class no-ops the SQLite-specific niceties so
  adapters implement only `store_item` + `retrieve_by_query`.
- **mem0 adapter** (`adapters.Mem0Backend`, `register_mem0_backend()`):
  subject_ref → user_id, metadata round-trip for category/frame filters,
  score clamped to the shared [0,1]/min_score scale, lazy import (mem0 is
  never a dependency), client injection for deterministic tests.
- **Embedders module** (`embedders.py`): `OpenAIEmbedder` (stdlib HTTP, zero
  deps, honors `OPENAI_BASE_URL` / `ACTIVEGRAPH_EMBEDDING_MODEL`),
  `HashEmbedder` (deterministic, for fixtures/tests), and
  `default_embedder_factory` — wired by the demo server at startup, so
  recall is hybrid whenever `OPENAI_API_KEY` is present and lexical (never
  erroring) otherwise.

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
