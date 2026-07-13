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
  extractor_version, config_hash, requested_facets, selection_id)`. Full-evidence
  runs use a null selection id. Same identity is a
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
- **Exact-span requests** — a domain/family projector may create a
  `selection_extraction_request` containing hash-verified character spans into
  one evidence revision. The caller owns selection policy; this pack still owns
  extractor routing, provider access, cache identity, annotation offsets, and
  settlement. Family-marked evidence never takes the eager whole-document path.

## The upgrade seam — `semantic.llm@0.1.0` (D025 stage two)

LLM-backed extraction registers through
`register_annotation_extractor(...)` with its own `extractor_id` — same
contract, same envelope, same cache identity and coverage semantics.

`semantic.llm@0.1.0` is registered beside the deterministic floor. It
implements `entity_mention` (people/orgs beyond regex reach),
`assertion` (with modality/polarity judgment), `preference_expression`,
and the two facets the floor does not: `relation_mention` and
`event_mention`.

- **Recorded provider seam.** With `llm_record_dir` set, every provider
  call replays from the prompt-hash-keyed record first; only unseen
  prompts reach the live provider (and are recorded before use).
  Re-extraction replays from cache and never re-contacts — a graph
  rebuild reproduces byte-equal annotations with no key present.
- **The LLM proposes, the extractor verifies.** Proposed spans are
  checked byte-for-byte against the content; non-matching spans are
  dropped. An LLM may not mint an annotation whose anchor doesn't exist.
- **Selection policy.** The `extraction_profile`'s `extractor_by_facet`
  map decides which extractor serves which facet per source category.
  With a provider configured (see `packs.llm_provider`), the seeded
  default routes `relation_mention`/`event_mention` to `semantic.llm`
  and keeps the deterministic floor for everything else (D041). With no
  provider the profile is unchanged and behavior is byte-identical to
  the zero-key mode.
- **Fork-trial-promote (ADR 0014).** The LLM extractor version lands as
  a *candidate* configuration for floor facets. `run_extractor_trial`
  compares deterministic-only vs LLM-upgraded drafts on recorded
  content and records the per-facet comparison as
  `extractor_promotion_evidence`; `promote_llm_extractor` re-routes
  facets only with that evidence plus a named approver.
- **No extra trust for fluency.** LLM annotations carry the extractor's
  (clamped) confidence and face exactly the same candidate/promotion
  gates and invalidation semantics.

## Invalidation

`invalidate_annotation_extractor` disables a version, invalidates its
runs and annotations, and demotes dependent candidates through
provenance (`projected_from_annotation`). Evidence is never touched
(ADR 0014).

## Guarded proceduralization — ADR 0029 pilot

`semantic.request_rule@0.1.0` is a shipped deterministic parser that may become
active only as a graph candidate earned from reviewed `semantic.llm@0.1.0`
annotations. `synthesize_request_procedure` declares schemas, objective,
effects, R0 ceiling, resource bounds, witness/held-out/counterexample sets,
guard, and exact fallback. `evaluate_request_procedure_fn(runtime, ...)` runs
the candidate in a real SQLite-backed runtime fork and fail-closed promotes only
the evaluation audit state. `promote_request_procedure` additionally requires a
named approver and perfect held-out exact-selector parity with zero false
admissions/rejections.

At serving time, the promoted procedure intercepts only its calibrated
communication request region. Guard abstention or drift records a deopt and
runs the LLM reference. Three distinct shape drifts demote the procedure. The
reference path remains teacher and fallback; no generated code is imported.

## What this pack deliberately does NOT do

- Migrate the existing `activity_normalizer` extraction path — that
  follows ADR 0026's ordering in a later workstream.
- Promote domain facts or tasks: those candidates still require their owning
  verdict/gate. Procedure promotion is a separate explicit, evidence-citing
  lifecycle and cannot promote its extracted content.
- Read or influence score, authority, or policy.
