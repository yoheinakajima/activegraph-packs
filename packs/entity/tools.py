"""Entity Pack tools — v0.1.

Provides helpers for entity registration and merge decision creation.
"""

from __future__ import annotations

from typing import Any, Optional

from activegraph.packs import tool


# ------------------------------------------------------------------ raw functions


def register_entity_fn(
    graph: Any,
    name: str,
    entity_type: str = "other",
    aliases: Optional[list] = None,
    identifiers: Optional[dict] = None,
    description: str = "",
    confidence: float = 0.9,
) -> Any:
    """Add an Entity to the graph and return the created object.

    Triggers entity_registry_recorder to index in local registry.
    Triggers merge_candidate_detector to check for duplicates.

    Returns:
        The created graph object.
    """
    return graph.add_object(
        "entity",
        {
            "name": name,
            "entity_type": entity_type,
            "aliases": aliases or [],
            "identifiers": identifiers or {},
            "confidence": confidence,
            "description": description,
            "source_ids": [],
            "merged_from_ids": [],
            "metadata": {},
        },
    )


def decide_merge_fn(
    graph: Any,
    merge_candidate_id: str,
    decision: str,
    surviving_entity_id: Optional[str] = None,
    rationale: str = "",
    decided_by: Optional[str] = None,
) -> Any:
    """Create a MergeDecision for a MergeCandidate.

    For 'accepted' decisions, also patches the surviving entity and
    creates a merged_into relation from the other entity.

    Args:
        graph: ActiveGraph Graph instance
        merge_candidate_id: ID of the MergeCandidate to decide on
        decision: 'accepted', 'rejected', or 'deferred'
        surviving_entity_id: For accepted merges, the entity that survives
        rationale: Why this decision was made
        decided_by: Principal ID or behavior name

    Returns:
        The created MergeDecision graph object.
    """
    decision_obj = graph.add_object(
        "merge_decision",
        {
            "merge_candidate_id": merge_candidate_id,
            "decision": decision,
            "surviving_entity_id": surviving_entity_id,
            "rationale": rationale,
            "decided_by": decided_by or "operator",
            "metadata": {},
        },
    )

    # Patch the merge_candidate status
    try:
        graph.patch_object(merge_candidate_id, {"status": decision})
    except Exception:
        pass

    # For accepted merges: create merged_into relation
    if decision == "accepted" and surviving_entity_id:
        try:
            candidate_obj = graph.get_object(merge_candidate_id)
            if candidate_obj:
                a_id = candidate_obj.data.get("entity_a_id")
                b_id = candidate_obj.data.get("entity_b_id")
                other_id = b_id if a_id == surviving_entity_id else a_id
                if other_id:
                    graph.add_relation(other_id, surviving_entity_id, "merged_into")
        except Exception:
            pass

    return decision_obj


# ------------------------------------------------------------------ tool wrappers


@tool(
    name="register_entity",
    description=(
        "Register a canonical Entity in the graph. "
        "Triggers entity_registry_recorder (local index) and "
        "merge_candidate_detector (dedup check). "
        "Returns the created entity object."
    ),
)
def register_entity(
    graph: Any,
    name: str,
    entity_type: str = "other",
    aliases: Optional[list] = None,
    identifiers: Optional[dict] = None,
    description: str = "",
) -> Any:
    """Registered tool wrapper."""
    return register_entity_fn(graph, name, entity_type, aliases, identifiers, description)


@tool(
    name="decide_merge",
    description=(
        "Create a MergeDecision for a MergeCandidate. "
        "Use decision='accepted' to merge, 'rejected' to keep separate, "
        "'deferred' to revisit later."
    ),
)
def decide_merge(
    graph: Any,
    merge_candidate_id: str,
    decision: str = "",
    surviving_entity_id: Optional[str] = None,
    rationale: str = "",
) -> Any:
    """Registered tool wrapper."""
    return decide_merge_fn(graph, merge_candidate_id, decision, surviving_entity_id, rationale)


TOOLS = [register_entity, decide_merge]
