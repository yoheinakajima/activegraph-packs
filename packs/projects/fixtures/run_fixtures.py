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
    return {"projects": 1, "candidates": 1}


if __name__ == "__main__":
    try:
        print(f"Projects Fixtures PASS: {run_fixture()}")
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)
