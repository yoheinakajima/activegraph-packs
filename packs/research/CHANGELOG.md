# Research Pack Changelog

## v0.1.1 — Relation integrity fix (2026-07-08)

### Fixed
- **`add_relation` argument order.** Relation writes passed
  `(type, source, target)` but the API is `(source, target, type)` — the same
  bug the Chat Pack fixed in its v0.2.0. Affected relations were being written
  as garbage edges (the type string as the source id), silently breaking graph
  traversal over this pack's audit trail. Part of a repo-wide sweep (80 calls
  across 14 packs) that also corrected fixture assertions written against the
  broken shape (`r.source` where `r.type` was meant).

## v0.1.0 — 2026-06-03

### Added
- `paper` object type: title, abstract, authors, venue, keywords, citations
- `author` object type: name, affiliation, h-index, paper list
- `venue` object type: name, kind (journal/conference/workshop/preprint)
- `method`, `benchmark`, `dataset` object types for research knowledge graph
- `idea_atom` object type: atomic research idea with novelty/coherence scores
- `research_direction` object type: synthesized direction from multiple idea atoms
- `experiment` object type: proposed or running experiment linked to a direction
- Relation types: `cites`, `authored_by`, `published_in`, `uses_method`, `reports_benchmark`, `uses_dataset`, `proposes_idea`, `composes_direction`, `tests_direction`, `derived_from_source`
- `paper_ingester` behavior: ingests `research_paper` sources into Paper + Author + Venue
- `claim_extractor` behavior: extracts claim observations from paper abstracts (mock)
- `idea_atom_extractor` behavior: distills idea atoms from paper keywords (mock)
- `hypothesis_generator` behavior: creates ResearchDirection from high-coherence atoms
- `research_direction_synthesizer` behavior: cross-paper direction synthesis
- Module-level registries with `clear_research_registry()` for fixture isolation
- Tools: `ingest_research_paper`, `create_idea_atom`, `create_experiment`
- Two fixtures covering paper ingestion pipeline and cross-paper direction synthesis

### Notes
- All LLM behaviors use deterministic mock stubs in v0.1
- Real LLM-powered extraction and hypothesis generation planned for v0.2
