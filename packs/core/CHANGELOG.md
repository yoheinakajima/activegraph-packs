# Core Pack Changelog

## v0.1.2 — Questions are not memory (2026-07-08)

### Fixed
- `_infer_category` checks for questions FIRST, before any keyword
  category. Keyword priority used to classify keyword-bearing questions as
  guidance ("Should I always use dark mode?" → instruction), which the
  memory pipeline then stored as standing guidance. A question is not a
  weaker candidate — it is not a candidate at all.

## v0.1.1 — Relation integrity fix (2026-07-08)

### Fixed
- **`add_relation` argument order.** Relation writes passed
  `(type, source, target)` but the API is `(source, target, type)` — the same
  bug the Chat Pack fixed in its v0.2.0. Affected relations were being written
  as garbage edges (the type string as the source id), silently breaking graph
  traversal over this pack's audit trail. Part of a repo-wide sweep (80 calls
  across 14 packs) that also corrected fixture assertions written against the
  broken shape (`r.source` where `r.type` was meant).
- `derived_from` relation type: added `source` to `source_types` and opened
  `target_types` (empty = any) — bridge packs point `derived_from` at domain
  objects Core must not enumerate. The previous closed list silently rejected
  every bridge-created edge once the argument order was fixed.

## v0.1.0 — Initial release (2026-06-03)

### Added
- 7 object types: `source`, `observation`, `task`, `action`, `artifact`, `memory_candidate`, `evaluation`
- 7 relation types: `grounds`, `produces`, `executes`, `generates`, `proposes`, `evaluates`, `derived_from`
- 3 deterministic behaviors:
  - `observation_extractor` — sentence splitting + heuristic scoring from sources
  - `task_linker` — Jaccard word-overlap linking of observations to open tasks
  - `memory_candidate_proposer` — proposes memory for high-confidence preference/decision/fact observations
- `CoreSettings` with configurable thresholds
- Fixture scenarios: `chat_observation_task`, `tool_result_source`, `artifact_generation`
- Full README with behavior map (Mermaid diagram)

### Design decisions
- Core is observation-first (not claim-first)
- No LLM in v0.1 — all behaviors are deterministic heuristics
- `task` is deliberately underpowered (Team/Ops Pack adds project management)
- `memory_candidate` only proposes — Memory Gateway decides acceptance
- `derived_from` relation enables bridge packs to connect domain objects to Core
