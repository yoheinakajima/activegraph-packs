# Changelog — semantic_extraction

## 0.1.0 — 2026-07-10

First implementation of the shared annotation layer (ADR 0026, slice 5a).

- `semantic_annotation` envelope over the ten standard facets; typed
  bodies for the six facets the deterministic v1 extractor implements.
- `semantic.deterministic@0.1.0`: byte-deterministic zero-key extractor
  (entities, assertions, questions, preferences, temporal expressions,
  topic tags) with a registry seam for LLM-upgraded extractors.
- Cache identity `(evidence_revision, extractor_id, extractor_version,
  config_hash, requested_facets)`; facet-incremental re-extraction.
- First-class `extraction_coverage` records + `annotation_coverage` tool.
- `extraction_profile` versioned config artifact (D042) seeded with the
  D041 eager floor; `update_extraction_profile` supersession tool.
- Profile- and memory-candidate projectors over annotations
  (`projected_from_annotation` provenance).
- `invalidate_annotation_extractor`: version demotion through provenance
  with evidence intact.
