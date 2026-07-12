"""Entity Pack behaviors — v0.1.

Four behaviors covering entity extraction, resolution, and dedup:

1. entity_registry_recorder — on entity.created, indexes the entity in
   the local registry for O(1) lookup by alias and identifier.

2. entity_extractor — on source.created, uses lightweight heuristics
   (capitalization patterns, email patterns, URL patterns) to extract
   EntityMention objects from source content.

3. entity_resolver — on entity_mention.created, matches the mention to
   an existing Entity in the registry. Creates a new Entity if no match
   exceeds the resolution_similarity_threshold. Links the mention to the
   entity via entity_id and refers_to relation.

4. merge_candidate_detector — on entity.created, compares the new entity
   against all registry entries to find high-similarity pairs and creates
   MergeCandidate objects.

Design rules:
- graph.objects() is UNSAFE in behaviors — local registry provides entity lookup
- Entity registry is keyed by entity_id; alias index provides name → [entity_id] mapping
- Similarity is computed from name/alias edit distance + identifier overlap
- merge_candidate_detector skips the entity just added (avoid self-comparison)
- All behaviors fail gracefully — entity resolution errors do not block pipeline
"""

from __future__ import annotations

import re
from typing import Any, Optional

from activegraph.packs import behavior

from .object_types import Entity, EntityMention, MergeCandidate
from .settings import EntitySettings


# ------------------------------------------------------------------ local registry
# entity_registry_recorder populates these; entity_resolver and
# merge_candidate_detector read from them. This avoids graph.objects() calls.

# entity_id → {name, aliases, identifiers, entity_type, ...}
_ENTITY_REGISTRY: dict[str, dict[str, Any]] = {}

# normalized_name/alias → [entity_id] — for fast name lookup
_ALIAS_INDEX: dict[str, list[str]] = {}


def _normalize(s: str) -> str:
    return s.strip().lower()


def _add_to_alias_index(entity_id: str, name: str, aliases: list[str]) -> None:
    for label in [name] + aliases:
        norm = _normalize(label)
        if norm:
            _ALIAS_INDEX.setdefault(norm, [])
            if entity_id not in _ALIAS_INDEX[norm]:
                _ALIAS_INDEX[norm].append(entity_id)


def clear_entity_registry() -> None:
    """Clear the local entity registry. Used in fixture teardown."""
    _ENTITY_REGISTRY.clear()
    _ALIAS_INDEX.clear()


# ------------------------------------------------------------------ similarity helpers


def _edit_distance_ratio(a: str, b: str) -> float:
    """Normalized edit distance: 1.0 = identical, 0.0 = completely different."""
    a, b = a.lower(), b.lower()
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    # Simple DP edit distance
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        new_dp = [i]
        for j in range(1, n + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            new_dp.append(min(new_dp[-1] + 1, dp[j] + 1, dp[j - 1] + cost))
        dp = new_dp
    distance = dp[n]
    max_len = max(m, n)
    return 1.0 - (distance / max_len)


def _identifier_overlap_score(ids_a: dict, ids_b: dict) -> float:
    """Score based on matching identifiers. Any exact match → 1.0."""
    if not ids_a or not ids_b:
        return 0.0
    for key in ids_a:
        if key in ids_b and ids_a[key].lower() == ids_b[key].lower():
            return 1.0  # Exact identifier match → definitive
    return 0.0


def _compute_similarity(
    name_a: str, aliases_a: list[str], ids_a: dict,
    name_b: str, aliases_b: list[str], ids_b: dict,
) -> tuple[float, list[str]]:
    """Compute similarity score and reasons between two entities."""
    reasons = []

    # Identifier overlap (highest weight)
    id_score = _identifier_overlap_score(ids_a, ids_b)
    if id_score >= 1.0:
        return 1.0, ["exact_identifier_match"]

    # Name/alias comparisons
    labels_a = [_normalize(name_a)] + [_normalize(a) for a in aliases_a]
    labels_b = [_normalize(name_b)] + [_normalize(b) for b in aliases_b]

    max_name_score = 0.0
    for la in labels_a:
        for lb in labels_b:
            if la == lb:
                reasons.append("name_exact_match")
                return 1.0, reasons
            score = _edit_distance_ratio(la, lb)
            if score > max_name_score:
                max_name_score = score

    if max_name_score >= 0.9:
        reasons.append("name_near_match")
    elif max_name_score >= 0.7:
        reasons.append("name_partial_match")

    final_score = max(id_score, max_name_score)
    return final_score, reasons


# ------------------------------------------------------------------ extraction heuristics

# Email pattern
_EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b')

# GitHub/URL repo pattern
_GITHUB_RE = re.compile(r'github\.com/([A-Za-z0-9\-_]+/[A-Za-z0-9\-_.]+)')

# Capitalized sequences likely to be person/org names (2+ words, each capitalized)
_CAPITALIZED_NAME_RE = re.compile(r'\b([A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20}){1,4})\b')

# Common org suffixes
_ORG_SUFFIXES = {
    "Inc", "LLC", "Ltd", "Corp", "Corporation", "Company", "Co",
    "Group", "Foundation", "Institute", "Labs", "Lab", "Technologies",
    "Tech", "Systems", "Solutions", "Ventures", "Capital", "Partners",
}


def _extract_mentions_from_text(
    text: str,
    source_id: str,
    settings: EntitySettings,
    max_mentions: int,
) -> list[dict]:
    """Extract entity mention candidates using heuristics."""
    mentions = []

    # --- Emails → person entities ---
    if settings.extract_persons:
        for m in _EMAIL_RE.finditer(text):
            email = m.group(0)
            local_part = email.split("@")[0].replace(".", " ").replace("_", " ").title()
            start = max(0, m.start() - 30)
            end = min(len(text), m.end() + 30)
            mentions.append({
                "text": email,
                "source_id": source_id,
                "entity_type_hint": "person",
                "confidence": 0.85,
                "context_snippet": text[start:end],
                "extraction_method": "regex",
                "metadata": {"normalized_name": local_part, "email": email},
            })
            if len(mentions) >= max_mentions:
                return mentions

    # --- Capitalized names → person or org ---
    if settings.extract_persons or settings.extract_organizations:
        seen_names = set()
        for m in _CAPITALIZED_NAME_RE.finditer(text):
            name = m.group(1).strip()
            if name in seen_names or len(name) < 4:
                continue
            seen_names.add(name)

            words = name.split()
            last_word = words[-1] if words else ""
            if last_word in _ORG_SUFFIXES or (len(words) >= 2 and words[-1] in _ORG_SUFFIXES):
                entity_type = "organization"
                confidence = 0.75
                if not settings.extract_organizations:
                    continue
            else:
                entity_type = "person"
                confidence = 0.65
                if not settings.extract_persons:
                    continue

            start = max(0, m.start() - 30)
            end = min(len(text), m.end() + 30)
            mentions.append({
                "text": name,
                "source_id": source_id,
                "entity_type_hint": entity_type,
                "confidence": confidence,
                "context_snippet": text[start:end],
                "extraction_method": "heuristic",
                "metadata": {},
            })
            if len(mentions) >= max_mentions:
                return mentions

    # --- GitHub URLs → repo entities ---
    if settings.extract_urls_as_entities:
        for m in _GITHUB_RE.finditer(text):
            repo = m.group(1)
            mentions.append({
                "text": repo,
                "source_id": source_id,
                "entity_type_hint": "repo",
                "confidence": 0.9,
                "context_snippet": m.group(0),
                "extraction_method": "regex",
                "metadata": {"github_url": f"https://github.com/{repo}"},
            })
            if len(mentions) >= max_mentions:
                return mentions

    return mentions


# ------------------------------------------------------------------ behaviors


@behavior(
    name="entity_registry_recorder",
    on=["object.created"],
    where={"object.type": "entity"},
    creates=[],
)
def entity_registry_recorder(event, graph, ctx, *, settings: EntitySettings):
    """Index a new entity in the local registry for fast lookup.

    On: object.created (entity)
    Creates: nothing — updates local _ENTITY_REGISTRY and _ALIAS_INDEX

    Called before entity_resolver and merge_candidate_detector so they
    can use the registry rather than graph.objects().
    """
    obj = event.payload.get("object", {})
    entity_id = obj.get("id")
    entity_data = obj.get("data", {})

    if not entity_id:
        return

    name = entity_data.get("name", "")
    aliases = entity_data.get("aliases", [])
    identifiers = entity_data.get("identifiers", {})
    entity_type = entity_data.get("entity_type", "other")

    _ENTITY_REGISTRY[entity_id] = {
        "id": entity_id,
        "name": name,
        "aliases": aliases,
        "identifiers": identifiers,
        "entity_type": entity_type,
    }
    _add_to_alias_index(entity_id, name, aliases)


@behavior(
    name="entity_extractor",
    on=["object.created"],
    where={"object.type": "source"},
    creates=["entity_mention"],
)
def entity_extractor(event, graph, ctx, *, settings: EntitySettings):
    """Extract entity mentions from a source using lightweight heuristics.

    On: object.created (source)
    Creates: entity_mention objects for each detected entity
    Creates: mentions(source → entity_mention) relations

    Uses regex and capitalization heuristics to find emails, person names,
    organization names, and optionally GitHub repos in source content.

    ADR 0026 step 4: this duplicate extraction path is DISABLED by
    default — entity-mention ownership moved to the shared extraction
    contract (entity_mention_from_annotation below). Enable
    ``extract_from_raw_sources`` only for pipelines whose sources do not
    flow through the shared layer yet.
    """
    if not settings.extract_from_raw_sources:
        return
    obj = event.payload.get("object", {})
    source_id = obj.get("id")
    source_data = obj.get("data", {})

    content = source_data.get("content", "")
    frame_id = source_data.get("frame_id")

    if not content or len(content.strip()) < 4:
        return

    candidates = _extract_mentions_from_text(
        text=content,
        source_id=source_id,
        settings=settings,
        max_mentions=settings.max_mentions_per_source,
    )

    for candidate in candidates:
        if candidate["confidence"] < settings.extraction_min_confidence:
            continue

        mention = graph.add_object(
            "entity_mention",
            EntityMention(
                text=candidate["text"],
                source_id=source_id,
                entity_id=None,
                entity_type_hint=candidate.get("entity_type_hint"),
                confidence=candidate["confidence"],
                context_snippet=candidate.get("context_snippet", ""),
                extraction_method=candidate.get("extraction_method", "heuristic"),
                frame_id=frame_id,
                metadata=candidate.get("metadata", {}),
            ).model_dump(),
        )

        # Create mentions relation: source → entity_mention
        try:
            graph.add_relation(source_id, mention.id, "mentions")
        except Exception:
            pass


# Map shared-layer entity_mention body kinds to this pack's entity types.
_ENTITY_TYPE_BY_ANNOTATION_KIND: dict[str, str] = {
    "person": "person",
    "organization": "organization",
    "place": "other",
    "product": "product",
    "proper_noun": "other",
    "handle": "person",
    "email": "person",
    "url": "other",
    "other": "other",
}


@behavior(
    name="entity_mention_from_annotation",
    on=["object.created"],
    where={
        "object.type": "semantic_annotation",
        "object.data.facet": "entity_mention",
        "object.data.status": "active",
    },
    view={"include_types": ["entity_mention"]},
    creates=["entity_mention"],
)
def entity_mention_from_annotation(event, graph, ctx, *, settings: EntitySettings):
    """Consume the shared layer's entity_mention annotations (ADR 0026
    step 4).

    Entity-mention *extraction* is owned by the extraction contract now;
    this behavior only materializes the pack's EntityMention object from
    an annotation so the resolver (canonical resolution — still solely
    this pack's) can run unchanged. Dedup is by annotation id.
    """
    if not settings.consume_entity_mention_annotations:
        return
    wrapper = event.payload.get("object", {})
    data = wrapper.get("data", {})
    if data.get("facet") != "entity_mention" or data.get("status") != "active":
        return
    confidence = data.get("confidence", 0.0)
    if confidence < settings.extraction_min_confidence:
        return
    annotation_id = wrapper.get("id")
    for existing in ctx.view.objects(type="entity_mention"):
        if (existing.data.get("metadata") or {}).get("annotation_id") == annotation_id:
            return

    body = data.get("body") or {}
    text = body.get("text", "")
    if not text:
        return
    kind = body.get("kind", "other")
    entity_type_hint = _ENTITY_TYPE_BY_ANNOTATION_KIND.get(kind, "other")
    metadata: dict[str, Any] = {
        "annotation_id": annotation_id,
        "annotation_identity": data.get("annotation_identity"),
        "kind": kind,
    }
    normalized = body.get("normalized") or text
    if kind == "email":
        local_part = text.split("@")[0].replace(".", " ").replace("_", " ").title()
        metadata["normalized_name"] = local_part
        metadata["email"] = normalized
    elif normalized != text:
        metadata["normalized_name"] = normalized

    evidence_id = data.get("evidence_id", "")
    selector = data.get("selector") or {}
    mention = graph.add_object(
        "entity_mention",
        EntityMention(
            text=text,
            source_id=evidence_id,
            entity_id=None,
            entity_type_hint=entity_type_hint,
            confidence=confidence,
            context_snippet=selector.get("exact", text),
            extraction_method="annotation",
            frame_id=None,
            metadata=metadata,
        ).model_dump(),
    )
    try:
        graph.add_relation(evidence_id, mention.id, "mentions")
    except Exception:
        pass


@behavior(
    name="entity_resolver",
    on=["object.created"],
    where={"object.type": "entity_mention"},
    creates=["entity"],
)
def entity_resolver(event, graph, ctx, *, settings: EntitySettings):
    """Match an entity mention to an existing entity or create a new one.

    On: object.created (entity_mention)
    Creates: entity (if no existing entity matches the mention)
    Side effects: patches entity_mention.entity_id and creates refers_to relation

    Uses the local entity registry to find candidate matches.
    Compares by name/alias similarity and identifier overlap.

    If auto_accept_exact_identifier_match is True, identifier matches bypass
    the similarity threshold and immediately link the mention.
    """
    obj = event.payload.get("object", {})
    mention_id = obj.get("id")
    mention_data = obj.get("data", {})

    mention_text = mention_data.get("text", "")
    mention_confidence = mention_data.get("confidence", 0.5)
    mention_meta = mention_data.get("metadata", {})
    entity_type_hint = mention_data.get("entity_type_hint", "other")
    source_id = mention_data.get("source_id", "")
    frame_id = mention_data.get("frame_id")

    if not mention_text:
        return

    # Extract identifiers from metadata (e.g., email)
    mention_ids: dict[str, str] = {}
    if "email" in mention_meta:
        mention_ids["email"] = mention_meta["email"]

    # Normalize mention name
    mention_norm = _normalize(mention_text)
    mention_name = mention_meta.get("normalized_name", mention_text)

    # --- Try to find a match in the registry ---
    best_entity_id: Optional[str] = None
    best_score = 0.0

    # Fast path: check alias index first
    alias_candidates = _ALIAS_INDEX.get(mention_norm, [])
    for eid in alias_candidates:
        entry = _ENTITY_REGISTRY.get(eid, {})
        score, _ = _compute_similarity(
            mention_name, [mention_text],
            mention_ids,
            entry.get("name", ""),
            entry.get("aliases", []),
            entry.get("identifiers", {}),
        )
        if score > best_score:
            best_score = score
            best_entity_id = eid
        if settings.auto_accept_exact_identifier_match and score >= 1.0:
            break

    # If no alias hit, scan all registry entries for identifier overlap
    if best_score < settings.resolution_similarity_threshold and mention_ids:
        for eid, entry in _ENTITY_REGISTRY.items():
            if eid in alias_candidates:
                continue
            id_score = _identifier_overlap_score(mention_ids, entry.get("identifiers", {}))
            if id_score > best_score:
                best_score = id_score
                best_entity_id = eid
            if settings.auto_accept_exact_identifier_match and id_score >= 1.0:
                break

    # --- Link to existing entity or create new one ---
    if best_entity_id and (
        best_score >= settings.resolution_similarity_threshold
        or (settings.auto_accept_exact_identifier_match and best_score >= 1.0)
    ):
        # Link mention to existing entity
        try:
            graph.patch_object(mention_id, {"entity_id": best_entity_id})
        except Exception:
            pass
        try:
            graph.add_relation(mention_id, best_entity_id, "refers_to")
        except Exception:
            pass
    else:
        # Create a new Entity for this mention
        aliases = []
        if mention_meta.get("normalized_name") and mention_meta["normalized_name"] != mention_text:
            aliases.append(mention_meta["normalized_name"])

        new_entity = graph.add_object(
            "entity",
            Entity(
                name=mention_name if mention_meta.get("normalized_name") else mention_text,
                entity_type=entity_type_hint or "other",
                aliases=aliases,
                identifiers=mention_ids,
                confidence=mention_confidence,
                source_ids=[source_id] if source_id else [],
                frame_id=frame_id,
                metadata={"created_by": "entity_resolver", "mention_id": mention_id},
            ).model_dump(),
        )

        # Link mention to new entity
        try:
            graph.patch_object(mention_id, {"entity_id": new_entity.id})
        except Exception:
            pass
        try:
            graph.add_relation(mention_id, new_entity.id, "refers_to")
        except Exception:
            pass


@behavior(
    name="merge_candidate_detector",
    on=["object.created"],
    where={"object.type": "entity"},
    creates=["merge_candidate"],
)
def merge_candidate_detector(event, graph, ctx, *, settings: EntitySettings):
    """Detect entities that may be duplicates of the newly created entity.

    On: object.created (entity)
    Creates: merge_candidate (for each sufficiently similar existing entity)
    Creates: merge_candidate_for(merge_candidate → entity) relations (both sides)

    Compares the new entity against all entries in the local registry
    (excluding itself). Creates a MergeCandidate for any pair whose
    similarity exceeds merge_candidate_threshold.
    """
    obj = event.payload.get("object", {})
    new_entity_id = obj.get("id")
    new_data = obj.get("data", {})

    if not new_entity_id:
        return

    new_name = new_data.get("name", "")
    new_aliases = new_data.get("aliases", [])
    new_ids = new_data.get("identifiers", {})
    frame_id = new_data.get("frame_id")

    # Compare against all existing registry entries (excluding the entity just added)
    for existing_id, entry in _ENTITY_REGISTRY.items():
        if existing_id == new_entity_id:
            continue

        score, reasons = _compute_similarity(
            new_name, new_aliases, new_ids,
            entry["name"], entry.get("aliases", []), entry.get("identifiers", {}),
        )

        if score >= settings.merge_candidate_threshold:
            candidate = graph.add_object(
                "merge_candidate",
                MergeCandidate(
                    entity_a_id=new_entity_id,
                    entity_b_id=existing_id,
                    similarity_score=score,
                    status="pending",
                    similarity_reasons=reasons,
                    frame_id=frame_id,
                ).model_dump(),
            )

            # Relations: merge_candidate_for → both entities
            try:
                graph.add_relation(candidate.id, new_entity_id, "merge_candidate_for")
            except Exception:
                pass
            try:
                graph.add_relation(candidate.id, existing_id, "merge_candidate_for")
            except Exception:
                pass


BEHAVIORS = [
    entity_registry_recorder,
    entity_extractor,
    entity_mention_from_annotation,
    entity_resolver,
    merge_candidate_detector,
]
