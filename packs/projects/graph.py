"""The work graph: containment, association, routing, views, and context
packets (ADR 0049).

Containment is a cycle-safe DAG with multiple parents and no stored depth
limit; every READ is explicitly bounded (depth + item budget). Context
follows typed reachability and routing provenance — never exact string
equality — and owner corrections become prediction-loop evidence. All of
it is replay-deterministic graph state.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

VIEW_COMPOSER = "projects.organizational_view@0.1.0"

#: Read bounds (ADR 0049 §1): the stored ontology has no depth limit, so
#: every traversal names its own.
DEFAULT_TRAVERSAL_DEPTH = 4
DEFAULT_TRAVERSAL_ITEMS = 200


def _stable(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()}"


def _active_project(reader, project_id: str):
    obj = reader.get_object(project_id) if hasattr(reader, "get_object") else None
    if obj is None or getattr(obj, "type", None) != "project":
        return None
    return obj


def _parents_of(reader, project_id: str) -> list[str]:
    return [
        relation.source
        for relation in reader.relations(target=project_id, type="workstream_contains")
        if not (relation.data or {}).get("removed")
    ]


def _children_of(reader, project_id: str) -> list[str]:
    return [
        relation.target
        for relation in reader.relations(source=project_id, type="workstream_contains")
        if not (relation.data or {}).get("removed")
    ]


def _ancestor_path(reader, start: str, wanted: str, *, limit: int = 500) -> Optional[list[str]]:
    """The containment path from ``wanted`` down to ``start`` if ``wanted``
    is an ancestor of ``start`` — the explainable half of cycle rejection."""
    frontier: list[list[str]] = [[start]]
    seen = {start}
    steps = 0
    while frontier and steps < limit:
        path = frontier.pop(0)
        for parent in _parents_of(reader, path[-1]):
            steps += 1
            if parent == wanted:
                return [wanted, *reversed(path)]
            if parent not in seen:
                seen.add(parent)
                frontier.append([*path, parent])
    return None


def link_workstreams_fn(
    graph, parent_id: str, child_id: str, *, actor: str = "owner", reader=None,
) -> dict[str, Any]:
    """Containment with multiple parents and EXPLAINABLE cycle rejection:
    a rejected link names the exact ancestor path that would close the
    loop (ADR 0049 §1)."""
    view = reader or graph
    parent = _active_project(view, parent_id)
    child = _active_project(view, child_id)
    if parent is None or child is None:
        return {"ok": False, "reason": "unknown_project"}
    if parent_id == child_id:
        return {"ok": False, "reason": "cycle_rejected",
                "cycle_path": [parent_id, parent_id],
                "explanation": "a workstream cannot contain itself"}
    cycle = _ancestor_path(view, parent_id, child_id)
    if cycle is not None:
        return {
            "ok": False, "reason": "cycle_rejected",
            "cycle_path": [*cycle, child_id],
            "explanation": (
                f"{child.data.get('name')!r} already contains "
                f"{parent.data.get('name')!r} through "
                f"{len(cycle) - 1} containment step(s); linking it as a "
                "child would close a loop"
            ),
        }
    existing = [
        relation for relation in view.relations(
            source=parent_id, target=child_id, type="workstream_contains",
        )
        if not (relation.data or {}).get("removed")
    ]
    if existing:
        return {"ok": True, "already_linked": True, "relation_id": existing[0].id}
    relation = graph.add_relation(
        parent_id, child_id, "workstream_contains",
        {"linked_by": actor}, actor=actor,
    )
    return {"ok": True, "relation_id": relation.id}


def unlink_workstreams_fn(
    graph, parent_id: str, child_id: str, *, actor: str = "owner", reader=None,
) -> dict[str, Any]:
    view = reader or graph
    for relation in view.relations(
        source=parent_id, target=child_id, type="workstream_contains",
    ):
        if (relation.data or {}).get("removed"):
            continue
        graph.remove_relation(relation.id, actor=actor)
        return {"ok": True, "relation_id": relation.id}
    return {"ok": False, "reason": "not_linked"}


def associate_workstream_fn(
    graph, project_id: str, entity_id: str, *, role: str = "involves",
    actor: str = "owner", evidence_refs: Optional[list[str]] = None, reader=None,
) -> dict[str, Any]:
    """Associate an entity with a workstream — the entity STAYS an entity
    (ADR 0049 §1): a company never becomes a project to render a tree."""
    view = reader or graph
    if _active_project(view, project_id) is None:
        return {"ok": False, "reason": "unknown_project"}
    entity = view.get_object(entity_id) if hasattr(view, "get_object") else None
    if entity is None or getattr(entity, "type", None) != "entity":
        return {"ok": False, "reason": "unknown_entity"}
    existing = [
        relation for relation in view.relations(
            source=project_id, target=entity_id,
            type="workstream_associated_with",
        )
        if not (relation.data or {}).get("removed")
    ]
    if existing:
        return {"ok": True, "already_associated": True,
                "relation_id": existing[0].id}
    relation = graph.add_relation(
        project_id, entity_id, "workstream_associated_with",
        {"role": str(role)[:80], "associated_by": actor,
         "evidence_refs": [str(r) for r in (evidence_refs or [])][:10]},
        actor=actor,
    )
    return {"ok": True, "relation_id": relation.id}


def route_item_fn(
    graph, item_ref: str, project_id: str, *, actor: str = "system",
    provenance: str = "", evidence_refs: Optional[list[str]] = None,
    confidence_milli: Optional[int] = None, reader=None,
) -> dict[str, Any]:
    """Route an item to a workstream with recorded provenance — the receipt
    the owner can always ask 'why did this land here?' against. A derived
    route may record the confidence its signals earned; an owner route
    needs none."""
    view = reader or graph
    if _active_project(view, project_id) is None:
        return {"ok": False, "reason": "unknown_project"}
    item = view.get_object(item_ref) if hasattr(view, "get_object") else None
    if item is None:
        return {"ok": False, "reason": "unknown_item"}
    existing = [
        relation for relation in view.relations(
            source=item_ref, target=project_id, type="routed_to",
        )
        if not (relation.data or {}).get("removed")
    ]
    if existing:
        return {"ok": True, "already_routed": True, "relation_id": existing[0].id}
    data: dict[str, Any] = {
        "routed_by": actor, "routing_provenance": str(provenance)[:200],
        "evidence_refs": [str(r) for r in (evidence_refs or [])][:6],
    }
    if confidence_milli is not None:
        data["confidence_milli"] = max(0, min(1000, int(confidence_milli)))
    relation = graph.add_relation(item_ref, project_id, "routed_to", data, actor=actor)
    return {"ok": True, "relation_id": relation.id}


def correct_routing_fn(
    graph, item_ref: str, *, to_project_id: Optional[str] = None,
    actor: str = "owner", reason: str = "", reader=None,
) -> dict[str, Any]:
    """The owner re-files (or un-files) an item. The correction is durable
    prediction evidence (ADR 0049 §4), and the context projection changes
    predictably with it."""
    view = reader or graph
    from_project = None
    for relation in view.relations(source=item_ref, type="routed_to"):
        if (relation.data or {}).get("removed"):
            continue
        from_project = relation.target
        graph.remove_relation(relation.id, actor=actor)
    result: dict[str, Any] = {"ok": True, "from_project_id": from_project}
    if to_project_id:
        routed = route_item_fn(
            graph, item_ref, to_project_id, actor=actor,
            provenance=f"owner correction: {reason}"[:200], reader=reader,
        )
        if not routed.get("ok"):
            return routed
        result["to_project_id"] = to_project_id
    correction = graph.add_object("routing_correction", {
        "correction_identity": _stable(
            "routing_correction", item_ref, from_project or "", to_project_id or "",
        ),
        "item_ref": item_ref,
        "from_project_id": from_project,
        "to_project_id": to_project_id,
        "kind": "reroute" if to_project_id else "unroute",
        "actor": actor,
        "reason": str(reason)[:300],
        "metadata": {},
    })
    result["correction_id"] = correction.id
    return result


def descendants_fn(
    reader, project_id: str, *, max_depth: int = DEFAULT_TRAVERSAL_DEPTH,
    max_items: int = DEFAULT_TRAVERSAL_ITEMS,
) -> dict[str, Any]:
    """Bounded, cycle-safe containment traversal (ADR 0049 §1): the stored
    graph has no depth limit, so the READ names its own and reports what
    the bounds excluded instead of pretending completeness."""
    rows: list[dict[str, Any]] = []
    seen = {project_id}
    frontier = [(project_id, 0)]
    truncated = False
    while frontier:
        current, depth = frontier.pop(0)
        if depth >= max_depth:
            if _children_of(reader, current):
                truncated = True
            continue
        for child in _children_of(reader, current):
            if child in seen:
                continue  # a diamond: multiple parents, one visit
            seen.add(child)
            if len(rows) >= max_items:
                truncated = True
                break
            obj = reader.get_object(child)
            rows.append({
                "project_id": child,
                "name": str((obj.data if obj else {}).get("name") or ""),
                "depth": depth + 1,
                "parents": _parents_of(reader, child),
            })
            frontier.append((child, depth + 1))
    return {
        "root": project_id,
        "descendants": rows,
        "bounds": {"max_depth": max_depth, "max_items": max_items},
        "truncated": truncated,
    }


# ---- organizational views (ADR 0049 §2) ----------------------------------------

def propose_organizational_view_fn(
    graph, *, name: str, perspective: str, roots: Optional[list[str]] = None,
    grouping_rules: Optional[list[dict[str, Any]]] = None,
    primary_paths: Optional[dict[str, str]] = None,
    labels: Optional[dict[str, str]] = None,
    ordering: str = "name",
    proposed_by: str = "agent",
    rationale: str = "",
    reader=None,
) -> dict[str, Any]:
    """Propose one view. The proposal is a candidate like any other: only
    the owner's promotion makes it active, and a successor supersedes —
    no silent rewrite of the active view."""
    view = reader or graph
    name = " ".join(str(name).split())
    if not name:
        raise ValueError("a view needs a name")
    existing = next(
        (obj for obj in view.objects(type="organizational_view")
         if obj.data.get("name") == name
         and obj.data.get("status") == "proposed"),
        None,
    )
    if existing is not None:
        return {"ok": True, "view_id": existing.id, "already_proposed": True}
    prior_versions = [
        int(obj.data.get("version") or 0)
        for obj in view.objects(type="organizational_view")
        if obj.data.get("name") == name
    ]
    version = max(prior_versions, default=0) + 1
    record = graph.add_object("organizational_view", {
        "view_identity": _stable("org_view", name, version),
        "name": name,
        "version": version,
        "perspective": str(perspective)[:80] or "custom",
        "roots": [str(r) for r in (roots or [])][:24],
        "grouping_rules": list(grouping_rules or [])[:8],
        "ordering": str(ordering)[:40],
        "labels": {str(k): str(v)[:80] for k, v in (labels or {}).items()},
        "primary_paths": {
            str(k): str(v) for k, v in (primary_paths or {}).items()
        },
        "status": "proposed",
        "proposed_by": proposed_by,
        "decided_by": "",
        "rationale": str(rationale)[:400],
        "supersedes": None,
        "metadata": {"composer": VIEW_COMPOSER},
    })
    return {"ok": True, "view_id": record.id, "version": version}


def review_organizational_view_fn(
    graph, view_ref: str, verdict: str, *,
    actor: str = "owner",
    edits: Optional[dict[str, Any]] = None,
    reader=None,
) -> dict[str, Any]:
    """Owner verdict on a proposed view: promote (optionally with edits) or
    reject. Promotion supersedes the previously promoted view of the same
    name — history stays reachable."""
    view = reader or graph
    record = view.get_object(view_ref) if hasattr(view, "get_object") else None
    if record is None or getattr(record, "type", None) != "organizational_view":
        record = next(
            (obj for obj in view.objects(type="organizational_view")
             if obj.data.get("view_identity") == view_ref),
            None,
        )
    if record is None:
        return {"ok": False, "reason": "view_not_found"}
    if record.data.get("status") != "proposed":
        return {"ok": False, "reason": "already_decided",
                "status": record.data.get("status")}
    if verdict not in ("promote", "reject"):
        raise ValueError("verdict must be promote | reject")
    if verdict == "reject":
        graph.patch_object(record.id, {
            "status": "rejected", "decided_by": actor,
        }, rationale=f"organizational view rejected by {actor}")
        return {"ok": True, "view_id": record.id, "status": "rejected"}
    allowed_edits = {}
    for key in ("roots", "grouping_rules", "primary_paths", "labels",
                "ordering", "name"):
        if edits and key in edits:
            allowed_edits[key] = edits[key]
    prior = next(
        (obj for obj in view.objects(type="organizational_view")
         if obj.data.get("status") == "promoted"
         and obj.data.get("name") == (allowed_edits.get("name")
                                      or record.data.get("name"))),
        None,
    )
    patch = {"status": "promoted", "decided_by": actor, **allowed_edits}
    if prior is not None and prior.id != record.id:
        patch["supersedes"] = str(prior.data.get("view_identity") or prior.id)
        graph.patch_object(prior.id, {"status": "superseded"},
                           rationale="a newer promoted view supersedes it")
    graph.patch_object(record.id, patch,
                       rationale=f"organizational view promoted by {actor}")
    return {"ok": True, "view_id": record.id, "status": "promoted",
            "edited": sorted(allowed_edits)}


def promoted_view_fn(reader, *, name: str = "") -> Optional[Any]:
    rows = [
        obj for obj in reader.objects(type="organizational_view")
        if obj.data.get("status") == "promoted"
        and (not name or obj.data.get("name") == name)
    ]
    rows.sort(key=lambda obj: int(obj.data.get("version") or 0))
    return rows[-1] if rows else None


def project_organizational_views_fn(reader) -> dict[str, Any]:
    views = [
        {
            "id": obj.id,
            "view_identity": obj.data.get("view_identity"),
            "name": obj.data.get("name"),
            "version": int(obj.data.get("version") or 0),
            "perspective": obj.data.get("perspective"),
            "roots": list(obj.data.get("roots") or []),
            "grouping_rules": list(obj.data.get("grouping_rules") or []),
            "primary_paths": dict(obj.data.get("primary_paths") or {}),
            "status": obj.data.get("status"),
            "proposed_by": obj.data.get("proposed_by"),
            "rationale": obj.data.get("rationale"),
        }
        for obj in reader.objects(type="organizational_view")
    ]
    views.sort(key=lambda row: (row["name"], row["version"]))
    return {"views": views}


# ---- the context packet (ADR 0049 §4) --------------------------------------------

def project_context_packet_fn(
    reader, project_id: str, *,
    max_depth: int = 2, max_items: int = 60,
    event_horizon: str = "",
) -> dict[str, Any]:
    """The provenance-bearing, bounded graph-reachability context for one
    workstream: the workstream, bounded descendants, associated entities
    with confirmed aliases, explicitly routed items, and people — with
    included/excluded refs and traversal bounds recorded. Exact-name
    matching appears nowhere in this function (ADR 0049 §4)."""
    workstream = _active_project(reader, project_id)
    if workstream is None:
        return {"exists": False, "project_id": project_id}
    included: list[str] = [project_id]
    excluded: dict[str, int] = {}

    tree = descendants_fn(
        reader, project_id, max_depth=max_depth, max_items=max_items,
    )
    scope_ids = [project_id] + [row["project_id"] for row in tree["descendants"]]
    included.extend(row["project_id"] for row in tree["descendants"])

    associations = []
    entity_ids: list[str] = []
    for scoped in scope_ids:
        for relation in reader.relations(
            source=scoped, type="workstream_associated_with",
        ):
            if (relation.data or {}).get("removed"):
                continue
            entity = reader.get_object(relation.target)
            if entity is None:
                continue
            entity_ids.append(entity.id)
            included.append(entity.id)
            associations.append({
                "entity_id": entity.id,
                "name": str((entity.data or {}).get("name") or ""),
                "entity_type": str((entity.data or {}).get("entity_type") or ""),
                "role": str((relation.data or {}).get("role") or ""),
                "via_project": scoped,
                "evidence_refs": list(
                    (relation.data or {}).get("evidence_refs") or []
                ),
            })

    # Confirmed aliases: owner-promoted facts naming the associated entities.
    alias_names = {row["name"].casefold() for row in associations if row["name"]}
    aliases = []
    for fact in reader.objects(type="subject_fact"):
        data = fact.data or {}
        if data.get("status") != "promoted":
            continue
        value = str(data.get("value") or "")
        if value and value.casefold() in alias_names:
            aliases.append({
                "attribute": data.get("attribute"),
                "value": value, "ref": fact.id,
            })
            included.append(fact.id)

    routed = []
    for scoped in scope_ids:
        for relation in reader.relations(target=scoped, type="routed_to"):
            if (relation.data or {}).get("removed"):
                continue
            if len(routed) >= max_items:
                excluded["routed_beyond_budget"] = (
                    excluded.get("routed_beyond_budget", 0) + 1
                )
                continue
            item = reader.get_object(relation.source)
            routed.append({
                "item_ref": relation.source,
                "item_type": getattr(item, "type", None),
                "project_id": scoped,
                "provenance": str((relation.data or {}).get("routing_provenance") or ""),
                "routed_by": str((relation.data or {}).get("routed_by") or ""),
            })
            included.append(relation.source)

    # People: person-typed entities associated with the scope.
    people = [
        row for row in associations
        if row["entity_type"] in ("person",)
    ]

    return {
        "exists": True,
        "project_id": project_id,
        "workstream": {
            "name": workstream.data.get("name"),
            "description": workstream.data.get("description"),
            "status": workstream.data.get("status"),
        },
        "descendants": tree["descendants"],
        "associations": associations,
        "aliases": aliases,
        "routed_items": routed,
        "people": people,
        "included_refs": list(dict.fromkeys(included))[:200],
        "excluded": {**excluded, **({"descendants_truncated": 1}
                                    if tree["truncated"] else {})},
        "traversal": {"max_depth": max_depth, "max_items": max_items},
        "event_horizon": event_horizon,
        "coverage": {
            "descendants": len(tree["descendants"]),
            "associations": len(associations),
            "routed_items": len(routed),
            "aliases": len(aliases),
        },
    }


__all__ = [
    "DEFAULT_TRAVERSAL_DEPTH",
    "DEFAULT_TRAVERSAL_ITEMS",
    "VIEW_COMPOSER",
    "associate_workstream_fn",
    "correct_routing_fn",
    "descendants_fn",
    "link_workstreams_fn",
    "project_context_packet_fn",
    "project_organizational_views_fn",
    "promoted_view_fn",
    "propose_organizational_view_fn",
    "review_organizational_view_fn",
    "route_item_fn",
    "unlink_workstreams_fn",
]
