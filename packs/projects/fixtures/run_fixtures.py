"""Projects conformance: explainable derivation and owner verdicts, zero-key."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parents[2]))

from activegraph import Graph, Runtime

from packs.projects import pack
from packs.projects.tools import (
    derive_project_candidates_fn,
    project_projects_fn,
    review_project_candidate_fn,
)
from packs.subject_profile import pack as subject_profile_pack


def run_fixture() -> dict:
    runtime = Runtime(Graph())
    runtime.load_pack(subject_profile_pack)
    runtime.load_pack(pack)
    runtime.run_until_idle()
    graph = runtime.graph

    graph.add_object("subject_fact", {
        "fact_identity": "fact:project", "subject_ref": "owner",
        "attribute": "project", "value": "Fixture Project",
        "text": "project: Fixture Project", "status": "promoted",
        "verdict_id": "verdict:seed",
    })
    derived = derive_project_candidates_fn(graph)
    assert derived["created"] == 1, "one fact-seeded candidate"
    candidate = next(obj for obj in graph.objects(type="project_candidate"))
    assert candidate.data["kind"] == "fact_seeded"
    assert candidate.data["sources"], "derivation stays explainable"

    confirmed = review_project_candidate_fn(
        graph, candidate.id, "confirm", actor="owner",
    )
    assert confirmed["status"] == "confirmed"
    projected = project_projects_fn(graph)
    assert len(projected["projects"]) == 1
    assert projected["projects"][0]["name"] == "Fixture Project"

    # Idempotence: re-derivation never duplicates or resurrects.
    again = derive_project_candidates_fn(graph)
    assert again["created"] == 0

    # The work graph (ADR 0049): containment rejects cycles explainably,
    # associations keep entities entities, and the context packet follows
    # reachability with explicit bounds.
    from packs.projects.graph import (
        associate_workstream_fn,
        descendants_fn,
        link_workstreams_fn,
        project_context_packet_fn,
        propose_organizational_view_fn,
        review_organizational_view_fn,
    )

    project = graph.get_object(confirmed["project_id"])
    child = graph.add_object("project", {
        "project_identity": "project:child", "name": "Fixture Child",
        "description": "sub-workstream", "status": "active",
        "seeded_from_candidate_id": None, "confirmed_by": "owner",
        "supersedes": None, "superseded_by": None, "metadata": {},
    })
    assert link_workstreams_fn(graph, project.id, child.id)["ok"]
    cycle = link_workstreams_fn(graph, child.id, project.id)
    assert cycle["reason"] == "cycle_rejected" and cycle["cycle_path"]
    tree = descendants_fn(graph, project.id, max_depth=3)
    assert [row["name"] for row in tree["descendants"]] == ["Fixture Child"]

    view = propose_organizational_view_fn(
        graph, name="By company", perspective="by_company",
        proposed_by="fixture",
    )
    assert review_organizational_view_fn(
        graph, view["view_id"], "promote",
    )["status"] == "promoted"

    packet = project_context_packet_fn(graph, project.id)
    assert packet["exists"] and packet["coverage"]["descendants"] == 1
    return {"projects": 1, "candidates": 1, "descendants": 1,
            "views_promoted": 1}


if __name__ == "__main__":
    try:
        print(f"Projects Fixtures PASS: {run_fixture()}")
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)
