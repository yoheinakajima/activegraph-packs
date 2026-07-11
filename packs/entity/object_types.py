"""Entity Pack object and relation types — v0.1.

Canonical, deduped representation of real-world entities.

Object types:
  entity           — A person, organization, project, product, repo, or other
  entity_mention   — A mention of an entity found in a source
  merge_candidate  — Two entities that may be the same (pending dedup)
  merge_decision   — A decision on a merge candidate

Design rules:
  - Entity is the canonical record; EntityMention provides provenance
  - Dedup is handled via MergeCandidate + MergeDecision (not auto-merge)
  - Entity.aliases includes all known names/handles for the entity
  - Entity.identifiers maps type → value (email, domain, github, etc.)
  - confidence reflects how sure we are this entity is real and unique

Integration:
  - Identity Pack: Principal.entity_id points to an Entity
  - Core: observation.metadata may reference entity_ids
  - VC/Research Pack: extends Entity with domain-specific overlays
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from activegraph.packs import ObjectType, RelationType


# ================================================================ Schemas


EntityTypeEnum = Literal[
    "person",
    "organization",
    "project",
    "product",
    "repo",
    "other",
]

MergeCandidateStatus = Literal["pending", "accepted", "rejected", "deferred"]


class Entity(BaseModel):
    """A canonical, deduped representation of a real-world entity.

    Entities aggregate all knowledge about a person, company, project,
    product, or repository. They are deduplicated through the
    MergeCandidate / MergeDecision flow.

    Use aliases to capture all known names and handles.
    Use identifiers to store typed unique IDs (email, domain, GitHub, etc.).
    """

    name: str = Field(description="Primary display name for this entity.")
    entity_type: EntityTypeEnum = Field(
        default="other",
        description="Type of real-world entity.",
    )
    aliases: list[str] = Field(
        default_factory=list,
        description=(
            "All known names, handles, and alternative spellings for this entity. "
            "Used for dedup matching. Include lowercase normalized versions."
        ),
    )
    identifiers: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Typed unique identifiers: "
            "{'email': 'alice@acme.com', 'domain': 'acme.com', "
            "'github': 'alicechan', 'linkedin': 'alice-chan'}."
        ),
    )
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description=(
            "Confidence that this entity record is accurate and unique. "
            "Decreases when there are unresolved merge candidates."
        ),
    )
    description: str = Field(default="", description="Short description of the entity.")
    source_ids: list[str] = Field(
        default_factory=list,
        description="IDs of sources where this entity was first encountered.",
    )
    merged_from_ids: list[str] = Field(
        default_factory=list,
        description="IDs of entities that were merged into this one.",
    )
    frame_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EntityMention(BaseModel):
    """A reference to an entity found in a source.

    EntityMentions provide provenance — they link an entity back to
    the source text where it was detected. The entity_extractor creates
    mentions; the entity_resolver links them to Entity objects.
    """

    text: str = Field(
        description="The exact text span that mentions the entity (as found in source)."
    )
    source_id: str = Field(
        description="ID of the source object where this mention was found."
    )
    entity_id: Optional[str] = Field(
        default=None,
        description=(
            "ID of the resolved Entity object. None if not yet resolved by entity_resolver."
        ),
    )
    entity_type_hint: Optional[EntityTypeEnum] = Field(
        default=None,
        description="Inferred entity type from extraction heuristics.",
    )
    confidence: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Confidence that this text span refers to a real entity.",
    )
    context_snippet: str = Field(
        default="",
        description="Surrounding text for context (up to 200 chars).",
    )
    extraction_method: str = Field(
        default="heuristic",
        description="How this mention was extracted: heuristic, regex, llm.",
    )
    frame_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MergeCandidate(BaseModel):
    """Two entities that may be the same, pending a dedup decision.

    Created by merge_candidate_detector when two entities have
    high name/alias/identifier similarity. Status starts as 'pending'
    and is resolved by a MergeDecision.
    """

    entity_a_id: str = Field(description="ID of the first Entity candidate.")
    entity_b_id: str = Field(description="ID of the second Entity candidate.")
    similarity_score: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Similarity score (0.0–1.0) based on name, alias, and identifier overlap. "
            "Higher = more likely to be the same entity."
        ),
    )
    status: MergeCandidateStatus = Field(
        default="pending",
        description="Dedup decision status: pending → accepted|rejected|deferred.",
    )
    similarity_reasons: list[str] = Field(
        default_factory=list,
        description=(
            "Reasons for the similarity score: "
            "['name_exact_match', 'email_match', 'alias_overlap', 'edit_distance_low']."
        ),
    )
    frame_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MergeDecision(BaseModel):
    """A decision on a MergeCandidate — merge, reject, or defer.

    Created by an operator or behavior after reviewing a MergeCandidate.
    'accepted' triggers the actual entity merge (surviving entity is the
    lower-cardinality / older one; other is marked merged_into).
    """

    merge_candidate_id: str = Field(description="ID of the MergeCandidate this decision is for.")
    decision: Literal["accepted", "rejected", "deferred"] = Field(
        description="The dedup decision.",
    )
    surviving_entity_id: Optional[str] = Field(
        default=None,
        description=(
            "For 'accepted' decisions: ID of the entity that survives the merge. "
            "The other entity is marked as merged_into this one."
        ),
    )
    rationale: str = Field(default="", description="Why this decision was made.")
    decided_by: Optional[str] = Field(
        default=None,
        description="Principal ID or behavior name that made this decision.",
    )
    frame_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ================================================================ ObjectType list

OBJECT_TYPES = [
    ObjectType(
        name="entity",
        schema=Entity,
        description=(
            "A canonical, deduped real-world entity: person, organization, "
            "project, product, repo, or other. Dedup via MergeCandidate flow."
        ),
    ),
    ObjectType(
        name="entity_mention",
        schema=EntityMention,
        description=(
            "A reference to an entity found in a source. Provides provenance. "
            "entity_id is set by entity_resolver after dedup."
        ),
    ),
    ObjectType(
        name="merge_candidate",
        schema=MergeCandidate,
        description=(
            "Two entities with high similarity, pending a dedup decision. "
            "Status: pending → accepted | rejected | deferred."
        ),
    ),
    ObjectType(
        name="merge_decision",
        schema=MergeDecision,
        description=(
            "A decision on a MergeCandidate — accepted merges the entities, "
            "rejected keeps them separate, deferred revisits later."
        ),
    ),
]


# ================================================================ RelationType list

RELATION_TYPES = [
    RelationType(
        name="mentions",
        # activity_evidence joined when entity-mention ownership moved to
        # the shared extraction contract (ADR 0026 step 4).
        source_types=("source", "activity_evidence"),
        target_types=("entity_mention",),
        description="A source or evidence revision contains an entity mention.",
    ),
    RelationType(
        name="refers_to",
        source_types=("entity_mention",),
        target_types=("entity",),
        description="An entity mention refers to a resolved entity.",
    ),
    RelationType(
        name="merge_candidate_for",
        source_types=("merge_candidate",),
        target_types=("entity",),
        description="A merge candidate involves an entity (use for both entity_a and entity_b).",
    ),
    RelationType(
        name="decided_by",
        source_types=("merge_decision",),
        target_types=("merge_candidate",),
        description="A merge decision resolves a merge candidate.",
    ),
    RelationType(
        name="merged_into",
        source_types=("entity",),
        target_types=("entity",),
        description="An entity was merged into another entity after a dedup decision.",
    ),
]
