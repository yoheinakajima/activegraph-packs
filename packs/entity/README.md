# Entity Pack — v0.1

**Canonical, deduped representation of real-world entities**: people, organizations, projects, products, and repos. Extracted from sources via lightweight heuristics, resolved against a local alias registry, and deduplicated via a MergeCandidate/MergeDecision flow.

---

## Object Types

| Type | Description |
|------|-------------|
| `entity` | Canonical entity: name, type, aliases, identifiers, confidence |
| `entity_mention` | A reference found in a source (provenance link) |
| `merge_candidate` | Two entities with high similarity, pending dedup |
| `merge_decision` | Accept, reject, or defer a merge candidate |

### Entity Types

`person` / `organization` / `project` / `product` / `repo` / `other`

---

## Behavior Map

```
entity.created
  → entity_registry_recorder
      Indexes entity in _ENTITY_REGISTRY + _ALIAS_INDEX (local module dict)
      Runs BEFORE entity_resolver so new entities are findable immediately
  → merge_candidate_detector
      Compares new entity against all registry entries (excluding itself)
      Name/alias edit distance + identifier overlap similarity
      If similarity ≥ merge_candidate_threshold → creates merge_candidate
      Creates: merge_candidate_for(candidate → entity) × 2

source.created
  → entity_extractor
      Regex: emails → person entities
      Regex: capitalized name sequences → person or organization
      Regex (optional): GitHub URLs → repo entities
      Confidence filtered by extraction_min_confidence
      Creates: entity_mention objects
      Creates: mentions(source → entity_mention)

entity_mention.created
  → entity_resolver
      Checks _ALIAS_INDEX for name matches
      Scans registry for identifier overlap (email, domain, etc.)
      If best_score ≥ resolution_similarity_threshold → links mention to existing entity
      If auto_accept_exact_identifier_match + score=1.0 → links immediately
      Otherwise → creates a new Entity
      Side effects: patches entity_mention.entity_id
      Creates: refers_to(entity_mention → entity)
```

---

## Dedup Flow

```
source → entity_extractor → entity_mention
  → entity_resolver → finds/creates entity
    → merge_candidate_detector → finds similar existing entities
      → merge_candidate (status=pending)
        → operator calls decide_merge_fn("accepted", surviving_entity_id=...)
          → merge_decision created
          → merge_candidate.status patched to "accepted"
          → merged_into(other_entity → surviving_entity) relation
```

---

## Settings

```python
EntitySettings(
    extraction_min_confidence=0.6,       # Below this → mention not created
    resolution_similarity_threshold=0.7, # Below this → new entity created
    merge_candidate_threshold=0.8,       # Below this → no merge_candidate
    auto_accept_exact_identifier_match=True,  # Email/domain exact → immediate link
    extract_persons=True,
    extract_organizations=True,
    extract_urls_as_entities=False,      # GitHub repos — disabled by default
    max_mentions_per_source=20,
)
```

---

## Registry Pattern

Entity resolution and merge detection use a **local in-memory registry** populated by `entity_registry_recorder`. This avoids `graph.objects()` calls in behaviors, which are unsafe.

Clear between tests: `from packs.entity.behaviors import clear_entity_registry; clear_entity_registry()`

---

## Quick Start

```python
from packs.entity import pack as entity_pack, EntitySettings
from packs.entity.behaviors import clear_entity_registry
from packs.entity.tools import register_entity_fn, decide_merge_fn

rt.load_pack(entity_pack, settings=EntitySettings())

# Extract entities from a source
graph.add_object("source", {
    "kind": "email",
    "content": "Alice Chen from Northwind Robotics sent alice@northwind.ai",
})
rt.run_until_idle()
# → entity_mention + entity objects in graph

# Pre-register a known entity (triggers merge detection against extracted entities)
org = register_entity_fn(graph, "Northwind Robotics", "organization",
                          identifiers={"domain": "northwind.ai"})
rt.run_until_idle()
# → merge_candidate if duplicate detected

# Resolve merge
candidates = list(graph.objects(type="merge_candidate"))
decide_merge_fn(graph, candidates[0].id, "accepted", surviving_entity_id=org.id)
```

---

## Composes With

- **Core Pack** — `entity_extractor` fires on `source.created`
- **Identity Pack** — `Principal.entity_id` points to an `Entity` for dedup across channels
- **Research/Codebase Packs** — extend `Entity` with domain overlays (team, papers, repos)

---

## Relation Types

| Relation | From → To | Meaning |
|----------|-----------|---------|
| `mentions` | source → entity_mention | Source contains a mention |
| `refers_to` | entity_mention → entity | Mention resolved to entity |
| `merge_candidate_for` | merge_candidate → entity | Candidate involves entity |
| `decided_by` | merge_decision → merge_candidate | Decision resolves candidate |
| `merged_into` | entity → entity | Entity absorbed into survivor |
