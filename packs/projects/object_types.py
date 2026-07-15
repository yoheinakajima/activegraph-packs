"""Project candidates, promoted projects, and the work graph (ADR 0049).

Canonical work organization is a typed graph: workstreams contain
workstreams (a cycle-free subgraph with multiple parents and no stored
depth limit), associate with entities without converting them, depend on
one another, receive routed items, and carry governed facets. Hierarchy
is never a parent pointer — it is a versioned, promoted organizational
VIEW over this graph.
"""

from __future__ import annotations

from typing import Any, Optional

from activegraph.packs import ObjectType, RelationType
from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCandidate(_StrictModel):
    """One proposed project with its explainable derivation."""

    candidate_identity: str = Field(min_length=1)
    name: str = Field(min_length=1)
    # label_seeded remains valid for replaying pre-ADR-0043 stores; new
    # derivations corroborate with labels instead of proposing from them.
    kind: str = Field(
        pattern="^(fact_seeded|synthesized|label_seeded|engagement_clustered|presence_clustered)$"
    )
    score_milli: int = Field(ge=0, le=1_000)
    sources: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    status: str = Field(
        default="proposed",
        pattern="^(proposed|confirmed|dismissed|superseded)$",
    )
    description: str = ""
    project_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Project(_StrictModel):
    """A canonical, owner-confirmed project."""

    project_identity: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    status: str = Field(default="active", pattern="^(active|archived|superseded)$")
    seeded_from_candidate_id: Optional[str] = None
    confirmed_by: str = Field(min_length=1)
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrganizationalView(_StrictModel):
    """A versioned, governed perspective over the work graph (ADR 0049 §2).

    A view selects roots, grouping rules, relationship paths, ordering,
    labels, and one primary display path per multi-parent node. Multiple
    views coexist; the agent proposes, the owner promotes/edits/rejects;
    no model rewrites the active view silently. A particular owner's
    hierarchy lives HERE as graph state — never in repository code.
    """

    view_identity: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    #: What perspective this view takes (owner-language, not an enum a
    #: model must fit): e.g. by_company, by_objective, this_quarter.
    perspective: str = Field(min_length=1)
    roots: list[str] = Field(default_factory=list)
    #: Grouping rules: ordered relation paths a renderer walks, e.g.
    #: [{"relation": "workstream_associated_with", "direction": "in"}].
    grouping_rules: list[dict[str, Any]] = Field(default_factory=list)
    ordering: str = "name"
    labels: dict[str, str] = Field(default_factory=dict)
    #: node id -> the ONE display parent when containment gives several.
    primary_paths: dict[str, str] = Field(default_factory=dict)
    status: str = Field(
        default="proposed",
        pattern="^(proposed|promoted|rejected|superseded)$",
    )
    proposed_by: str = Field(min_length=1)
    decided_by: str = ""
    rationale: str = ""
    supersedes: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RoutingCorrection(_StrictModel):
    """An owner correction to association/routing (ADR 0049 §4): the
    prediction-loop evidence a silent re-file would throw away."""

    correction_identity: str = Field(min_length=1)
    item_ref: str = Field(min_length=1)
    from_project_id: Optional[str] = None
    to_project_id: Optional[str] = None
    kind: str = Field(
        default="reroute", pattern="^(reroute|unroute|associate|dissociate)$"
    )
    actor: str = Field(min_length=1)
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectFacet(_StrictModel):
    """A governed cross-cutting classification — never fake containment."""

    facet_identity: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    created_by: str = Field(min_length=1)
    status: str = Field(default="active", pattern="^(active|retired)$")
    metadata: dict[str, Any] = Field(default_factory=dict)


OBJECT_TYPES = [
    ObjectType("project_candidate", ProjectCandidate, "A proposed project with explainable sources."),
    ObjectType("project", Project, "An owner-confirmed canonical project."),
    ObjectType(
        "organizational_view", OrganizationalView,
        "A versioned, promoted perspective over the canonical work graph.",
    ),
    ObjectType(
        "routing_correction", RoutingCorrection,
        "An owner correction to routing/association — prediction evidence.",
    ),
    ObjectType(
        "project_facet", ProjectFacet,
        "A governed cross-cutting classification for workstreams.",
    ),
]

#: The neutral typed relations (ADR 0049 §1). Containment is a directed
#: acyclic subgraph — multiple parents legal, cycles rejected at write —
#: while depends_on/related_to need not be acyclic. Companies, people,
#: and topics stay entities: association never converts them.
RELATION_TYPES = [
    RelationType(
        name="workstream_contains",
        source_types=("project",),
        target_types=("project",),
        description="A workstream contains a sub-workstream (DAG; multiple parents legal).",
    ),
    RelationType(
        name="workstream_associated_with",
        source_types=("project",),
        target_types=("entity",),
        description="A workstream is associated with an entity, with a relationship role.",
    ),
    RelationType(
        name="workstream_depends_on",
        source_types=("project",),
        target_types=("project",),
        description="A workstream depends on another workstream.",
    ),
    RelationType(
        name="workstream_related_to",
        source_types=("project",),
        target_types=("project",),
        description="A workstream is related to another workstream (non-hierarchical).",
    ),
    RelationType(
        name="routed_to",
        source_types=(
            "activity_evidence", "source_item_summary", "conversation_thread",
            "conversation_message", "information_access_hint", "subject_fact",
        ),
        target_types=("project",),
        description="An item/evidence is routed to a workstream, with provenance.",
    ),
    RelationType(
        name="classified_by",
        source_types=("project",),
        target_types=("project_facet",),
        description="A workstream carries a governed cross-cutting facet.",
    ),
]

__all__ = [
    "ProjectCandidate", "Project", "OrganizationalView", "RoutingCorrection",
    "ProjectFacet", "OBJECT_TYPES", "RELATION_TYPES",
]
