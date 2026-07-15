"""ADR 0049: work organization is a graph with promoted views.

Containment is a cycle-safe DAG with multiple parents and no stored depth
limit; reads are explicitly bounded; entities associate without becoming
projects; organizational views are proposed/promoted/superseded governed
artifacts; the context packet follows typed reachability and provenance —
never exact-name matching; and owner routing corrections are durable
prediction evidence.
"""

from __future__ import annotations

import pytest

from activegraph import Graph, Runtime

from packs.entity import pack as entity_pack
from packs.projects import pack as projects_pack
from packs.projects.graph import (
    associate_workstream_fn,
    correct_routing_fn,
    descendants_fn,
    link_workstreams_fn,
    project_context_packet_fn,
    project_organizational_views_fn,
    promoted_view_fn,
    propose_organizational_view_fn,
    review_organizational_view_fn,
    route_item_fn,
    unlink_workstreams_fn,
)
from packs.subject_profile import pack as subject_profile_pack


@pytest.fixture
def runtime():
    rt = Runtime(Graph())
    rt.load_pack(subject_profile_pack)
    rt.load_pack(entity_pack)
    rt.load_pack(projects_pack)
    rt.run_until_idle()
    return rt


def _project(graph, name: str):
    return graph.add_object("project", {
        "project_identity": f"project:{name}",
        "name": name,
        "description": f"{name} workstream",
        "status": "active",
        "seeded_from_candidate_id": None,
        "confirmed_by": "owner",
        "supersedes": None,
        "superseded_by": None,
        "metadata": {},
    })


def _entity(graph, name: str, entity_type: str = "organization"):
    return graph.add_object("entity", {
        "name": name,
        "entity_type": entity_type,
        "aliases": [],
    })


# ---- containment ---------------------------------------------------------------

def test_multiple_parents_are_legal_and_traversal_visits_once(runtime):
    graph = runtime.graph
    portfolio = _project(graph, "Portfolio Support")
    advising = _project(graph, "Advising")
    shared = _project(graph, "Board Prep")
    assert link_workstreams_fn(graph, portfolio.id, shared.id)["ok"]
    assert link_workstreams_fn(graph, advising.id, shared.id)["ok"]
    again = link_workstreams_fn(graph, portfolio.id, shared.id)
    assert again["already_linked"], "re-linking coalesces, never duplicates"

    tree = descendants_fn(graph, portfolio.id, max_depth=3)
    assert [row["project_id"] for row in tree["descendants"]] == [shared.id]
    assert set(tree["descendants"][0]["parents"]) == {portfolio.id, advising.id}


def test_cycles_reject_with_an_explainable_path(runtime):
    graph = runtime.graph
    a = _project(graph, "Alpha")
    b = _project(graph, "Beta")
    c = _project(graph, "Gamma")
    link_workstreams_fn(graph, a.id, b.id)
    link_workstreams_fn(graph, b.id, c.id)

    rejected = link_workstreams_fn(graph, c.id, a.id)
    assert rejected["ok"] is False
    assert rejected["reason"] == "cycle_rejected"
    assert rejected["cycle_path"][0] == a.id and rejected["cycle_path"][-1] == a.id
    assert "would close a loop" in rejected["explanation"]

    selfie = link_workstreams_fn(graph, a.id, a.id)
    assert selfie["reason"] == "cycle_rejected"

    # Unlink releases the constraint; deep nesting has no stored limit.
    unlink_workstreams_fn(graph, a.id, b.id)
    assert link_workstreams_fn(graph, c.id, a.id)["ok"]


def test_traversal_bounds_are_explicit_and_honest(runtime):
    graph = runtime.graph
    root = _project(graph, "Root")
    current = root
    for index in range(6):
        child = _project(graph, f"Level {index + 1}")
        link_workstreams_fn(graph, current.id, child.id)
        current = child
    shallow = descendants_fn(graph, root.id, max_depth=2)
    assert len(shallow["descendants"]) == 2
    assert shallow["truncated"] is True, "bounds report what they cut"
    deep = descendants_fn(graph, root.id, max_depth=10)
    assert len(deep["descendants"]) == 6
    assert deep["truncated"] is False


def test_association_never_converts_the_entity(runtime):
    graph = runtime.graph
    portfolio = _project(graph, "Portfolio Support")
    company = _entity(graph, "ExampleCorp")
    result = associate_workstream_fn(
        graph, portfolio.id, company.id, role="portfolio_company",
        evidence_refs=["mail:1"],
    )
    assert result["ok"]
    assert graph.get_object(company.id).type == "entity", \
        "a company stays an entity whatever the view renders"
    assert associate_workstream_fn(
        graph, portfolio.id, company.id,
    )["already_associated"]


# ---- organizational views ---------------------------------------------------------

def test_views_propose_promote_edit_and_supersede(runtime):
    graph = runtime.graph
    portfolio = _project(graph, "Portfolio Support")
    proposed = propose_organizational_view_fn(
        graph, name="By company", perspective="by_company",
        roots=[portfolio.id],
        grouping_rules=[{"relation": "workstream_associated_with",
                         "direction": "out"}],
        proposed_by="agent",
        rationale="companies group the active work",
    )
    assert proposed["ok"] and proposed["version"] == 1
    assert propose_organizational_view_fn(
        graph, name="By company", perspective="by_company", proposed_by="agent",
    )["already_proposed"]

    promoted = review_organizational_view_fn(
        graph, proposed["view_id"], "promote",
        edits={"ordering": "recent"},
    )
    assert promoted["status"] == "promoted"
    assert promoted["edited"] == ["ordering"]
    active = promoted_view_fn(graph)
    assert active is not None and active.data["ordering"] == "recent"

    successor = propose_organizational_view_fn(
        graph, name="By company", perspective="by_company", proposed_by="agent",
        rationale="second draft",
    )
    assert successor["version"] == 2
    review_organizational_view_fn(graph, successor["view_id"], "promote")
    views = project_organizational_views_fn(graph)["views"]
    by_version = {row["version"]: row["status"] for row in views}
    assert by_version == {1: "superseded", 2: "promoted"}, \
        "promotion supersedes; nothing silently rewrites the active view"

    rejected = propose_organizational_view_fn(
        graph, name="By horizon", perspective="this_quarter", proposed_by="agent",
    )
    assert review_organizational_view_fn(
        graph, rejected["view_id"], "reject",
    )["status"] == "rejected"


# ---- the context packet -------------------------------------------------------------

def _seed_fact(graph, attribute: str, value: str):
    graph.add_object("subject_fact", {
        "fact_identity": f"fact:{attribute}:{value}",
        "subject_ref": "owner",
        "attribute": attribute,
        "value": value,
        "text": f"{attribute}: {value}",
        "status": "promoted",
        "verdict_id": "verdict:seed",
    })


def test_context_follows_graph_reachability_not_names(runtime):
    """THE renamed-bucket case (ADR 0049 §4): 'Portfolio Support' receives
    its associated company's evidence with zero string equality between
    the project name and anything in the evidence."""
    graph = runtime.graph
    portfolio = _project(graph, "Portfolio Support")
    company = _entity(graph, "ExampleCorp")
    associate_workstream_fn(
        graph, portfolio.id, company.id, role="portfolio_company",
    )
    _seed_fact(graph, "company", "ExampleCorp")
    summary = graph.add_object("source_item_summary", {
        "summary_identity": "leaf:1",
        "request_id": "request:1",
        "recipe_id": "gmail_sent_v1",
        "item_ref": "message:1",
        "evidence_refs": ["evidence:1"],
        "fields": {"projects": ["ExampleCorp expansion"],
                   "authored_intent": "intro to a portfolio company"},
    })
    route_item_fn(
        graph, summary.id, portfolio.id,
        provenance="mentions ExampleCorp, associated with this workstream",
    )
    # An unrelated global hint exists but is NOT routed here.
    graph.add_object("information_access_hint", {
        "hint_identity": "hint:global",
        "subject_ref": "owner",
        "question_class": "anything",
        "source": "gmail",
        "strategy": "search everything",
        "accepted_by": "owner",
        "status": "active",
    })

    packet = project_context_packet_fn(graph, portfolio.id)
    assert packet["exists"]
    assert [row["name"] for row in packet["associations"]] == ["ExampleCorp"]
    assert [row["value"] for row in packet["aliases"]] == ["ExampleCorp"]
    assert [row["item_ref"] for row in packet["routed_items"]] == [summary.id]
    assert packet["routed_items"][0]["provenance"].startswith("mentions ExampleCorp")
    # Isolation: the unrouted global hint is nowhere in this packet.
    assert not any("hint" in str(ref) for ref in packet["included_refs"])
    assert packet["coverage"]["routed_items"] == 1
    assert packet["traversal"] == {"max_depth": 2, "max_items": 60}


def test_owner_routing_correction_changes_context_predictably(runtime):
    graph = runtime.graph
    portfolio = _project(graph, "Portfolio Support")
    advising = _project(graph, "Advising")
    summary = graph.add_object("source_item_summary", {
        "summary_identity": "leaf:2",
        "request_id": "request:1",
        "recipe_id": "gmail_sent_v1",
        "item_ref": "message:2",
        "evidence_refs": ["evidence:2"],
        "fields": {},
    })
    route_item_fn(graph, summary.id, portfolio.id, provenance="first guess")
    corrected = correct_routing_fn(
        graph, summary.id, to_project_id=advising.id,
        actor="owner", reason="this belongs under advising",
    )
    assert corrected["ok"]
    assert corrected["from_project_id"] == portfolio.id
    before = project_context_packet_fn(graph, portfolio.id)
    after = project_context_packet_fn(graph, advising.id)
    assert before["routed_items"] == []
    assert [row["item_ref"] for row in after["routed_items"]] == [summary.id]
    correction = next(obj for obj in graph.objects(type="routing_correction"))
    assert correction.data["kind"] == "reroute"
    assert correction.data["actor"] == "owner"
    assert correction.data["reason"] == "this belongs under advising"
