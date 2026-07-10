# Semantic Extraction Pack

The shared annotation layer (ADR 0026): extract once into typed,
source-anchored annotations; let every consumer project from them.

## What it owns

- **Annotation contract** — one provenance envelope
  (`semantic_annotation`) shared by all typed facet bodies. Standard
  facets: `entity_mention`, `assertion`, `question`, `idea`,
  `event_mention`, `relation_mention`, `preference_expression`,
  `temporal_expression`, `quantity_mention`, `topic_tag` (all defined;
  the deterministic v1 extractor implements the bootstrap subset —
  requesting an unimplemented facet is recorded in coverage as
  `not_implemented`, never silently claimed). Every annotation carries
  evidence id + revision, an exact `char_span` selector, extractor
  id/version/config hash, confidence, author-vs-subject attribution,
  event time vs observation time, modality, polarity, and invalidation
  status.
- **Deterministic extractor v1** (`semantic.deterministic@0.1.0`) — the
  zero-key floor: entity mentions (handles, emails, URLs, proper-noun
  heuristics), sentence-level assertions, pattern-based preference
  expressions, deterministic temporal expressions (ISO + unambiguous
  natural dates), questions, topic tags. Byte-deterministic; no
  wall-clock; no network.
- **Cache identity** — `(evidence_revision, extractor_id,
  extractor_version, config_hash, requested_facets)`. Same identity is a
  no-op; a wider facet set executes only what no prior run of the same
  extractor identity produced.
- **Coverage** — every run records what was and wasn't processed
  (`extraction_coverage`), first-class and queryable via the
  `annotation_coverage` tool.
- **`extraction_profile`** — the versioned, owner-editable,
  supersedable config artifact (D042) declaring required facets per
  source category. v1 seeds the D041 eager floor (entities, assertions,
  preferences, questions, explicit dates). Nothing about *which facets
  when* is hardcoded.
- **Candidate projectors** — profile-candidate and memory-candidate
  projectors consuming annotations (never raw evidence). Extraction
  produces annotations, never domain candidates; projectors are
  separate, per-domain policy. Promotion gates downstream are unchanged;
  annotations dedupe within one evidence revision; each domain still
  dedupes its own records.

## The upgrade seam

LLM-backed extraction registers through
`register_annotation_extractor(...)` with its own `extractor_id` — same
contract, same envelope, same cache identity and coverage semantics.
Selecting it is `SemanticExtractionSettings.extractor_id` /
`extractor_version` (config, not code).

## Invalidation

`invalidate_annotation_extractor` disables a version, invalidates its
runs and annotations, and demotes dependent candidates through
provenance (`projected_from_annotation`). Evidence is never touched
(ADR 0014).

## What this pack deliberately does NOT do

- Migrate the existing `activity_normalizer` extraction path — that
  follows ADR 0026's ordering in a later workstream.
- Promote anything: candidates stay candidates until a verdict or a
  governed gate.
- Read or influence score, authority, or policy.
