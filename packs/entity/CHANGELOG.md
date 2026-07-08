# Entity Pack Changelog

## v0.1.2 — activegraph 1.3 compatibility (2026-07-08)

### Fixed
- `@tool` wrapper signatures satisfy the runtime's v1.3 registration-time
  validation: every parameter beyond the `(args, ctx)` invocation contract
  now has a default. No behavior change (behaviors call the `_fn`
  variants directly).

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
- 4 object types: `entity`, `entity_mention`, `merge_candidate`, `merge_decision`
- 5 relation types: `mentions`, `refers_to`, `merge_candidate_for`, `decided_by`, `merged_into`
- 4 behaviors:
  - `entity_registry_recorder` — on `entity.created`: indexes entity in `_ENTITY_REGISTRY` and `_ALIAS_INDEX` for O(1) lookup; runs before `entity_resolver` and `merge_candidate_detector`
  - `entity_extractor` — on `source.created`: extracts `entity_mention` objects using regex (emails → person, capitalized name sequences → person/organization, GitHub URLs → repo); confidence-filtered by `extraction_min_confidence`
  - `entity_resolver` — on `entity_mention.created`: matches mention against registry via alias index + identifier overlap; links to existing entity or creates a new one; patches `entity_mention.entity_id`
  - `merge_candidate_detector` — on `entity.created`: compares new entity against all registry entries using name/alias edit distance + identifier overlap; creates `merge_candidate` for pairs above `merge_candidate_threshold`
- `EntitySettings` with `extraction_min_confidence`, `resolution_similarity_threshold`, `merge_candidate_threshold`, `auto_accept_exact_identifier_match`, `extract_persons`, `extract_organizations`, `extract_urls_as_entities`, `max_mentions_per_source`
- Tool functions: `register_entity_fn`, `decide_merge_fn`
- Fixture scenarios: entity extraction from email, person/org resolution, merge candidate detection and decision
- Full README with dedup flow diagram

### Design decisions
- All behavior-context entity lookups go through the local `_ENTITY_REGISTRY` / `_ALIAS_INDEX` — `graph.objects()` is never called inside behaviors
- `entity_registry_recorder` is listed first in `BEHAVIORS` to ensure the registry is populated before resolver and merge detector run on the same event batch
- Identifier exact-match (email, domain) bypasses the edit distance threshold and immediately links mention to entity when `auto_accept_exact_identifier_match=True`
- Clear between tests: `clear_entity_registry()`
