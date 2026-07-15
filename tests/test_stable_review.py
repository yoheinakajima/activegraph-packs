"""ADR 0048 §3–4: stable review snapshots and the typed correction grammar.

Once review starts (first verdict, or an explicit begin), the draft freezes:
a newer synthesis can neither replace nor re-key reviewed items — changed
and new material arrives as an additive, evidence-linked understanding
delta the owner applies, dismisses, or defers. Unchanged semantic items
retain their decisions; renamed/edited/split/merged/conflicting material
never inherits a verdict it did not earn.
"""

from __future__ import annotations

import pytest

from activegraph import Graph, Runtime

from packs.projects import pack as projects_pack
from packs.subject_profile import pack as subject_profile_pack
from packs.subject_synthesis import pack as subject_synthesis_pack
from packs.subject_synthesis.draft import (
    apply_understanding_delta_fn,
    begin_setup_review_fn,
    comment_setup_item_fn,
    compose_deterministic_draft_fn,
    current_setup_draft_fn,
    dismiss_understanding_delta_fn,
    project_setup_draft_fn,
    review_setup_item_fn,
    semantic_item_key,
    split_setup_project_item_fn,
)


@pytest.fixture
def runtime():
    rt = Runtime(Graph())
    rt.load_pack(subject_profile_pack)
    rt.load_pack(projects_pack)
    rt.load_pack(subject_synthesis_pack)
    rt.run_until_idle()
    return rt


def _plant_candidates(graph, *names: str) -> None:
    for name in names:
        graph.add_object("project_candidate", {
            "candidate_identity": f"candidate:{name}",
            "name": name,
            "kind": "fact_seeded",
            "score_milli": 900,
            "sources": [f"fact:{name}"],
            "rationale": "seeded for the stable-review tests",
            "status": "proposed",
            "project_id": None,
            "metadata": {},
        })


# ---- semantic identity --------------------------------------------------------

def test_semantic_keys_track_content_not_position():
    same = semantic_item_key("projects", {"name": "BabyAGI", "description": "a"})
    assert same == semantic_item_key(
        "projects", {"name": " babyagi ", "description": "totally different"}
    ), "a project's identity is its normalized name, not its description"
    assert same != semantic_item_key("projects", {"name": "BabyAGI 2"})
    assert semantic_item_key(
        "identity", {"attribute": "role", "value": "GP"}
    ) != semantic_item_key("identity", {"attribute": "role", "value": "Advisor"}), \
        "an edited identity value is a NEW semantic item"
    assert semantic_item_key(
        "access", {"source": "gmail", "strategy": "search in:sent",
                   "question_class": "a"}
    ) == semantic_item_key(
        "access", {"source": "gmail", "strategy": "search in:sent",
                   "question_class": "b"}
    )


# ---- the freeze ---------------------------------------------------------------

def test_unreviewed_head_still_supersedes(runtime):
    graph = runtime.graph
    _plant_candidates(graph, "Alpha")
    first = compose_deterministic_draft_fn(graph)
    _plant_candidates(graph, "Beta")
    second = compose_deterministic_draft_fn(graph)
    assert second.get("draft_id") != first["draft_id"]
    head = current_setup_draft_fn(graph)
    assert int(head.data["version"]) == 2
    old = graph.get_object(first["draft_id"])
    assert old.data["status"] == "superseded", \
        "with no owner decisions there is nothing to preserve"


def test_first_verdict_freezes_the_snapshot(runtime):
    graph = runtime.graph
    _plant_candidates(graph, "Alpha", "Beta")
    compose_deterministic_draft_fn(graph)
    head = current_setup_draft_fn(graph)
    items = project_setup_draft_fn(graph)["items"]
    alpha = next(i for i in items if i["proposed"].get("name") == "Alpha")
    review_setup_item_fn(graph, alpha["id"], "accept")

    # A later source lands; synthesis recomposes — but review started.
    _plant_candidates(graph, "Gamma")
    result = compose_deterministic_draft_fn(graph)
    assert result.get("delta_id"), "a frozen head yields a delta, not a version"
    assert result.get("draft_id") is None or result["draft_id"] == head.id
    still_head = current_setup_draft_fn(graph)
    assert still_head.id == head.id, "the reviewed snapshot did not move"
    assert int(still_head.data["version"]) == 1

    projected = project_setup_draft_fn(graph)
    accepted = next(i for i in projected["items"]
                    if i["proposed"].get("name") == "Alpha")
    assert accepted["status"] == "accepted", "the verdict survived recomposition"
    deltas = projected["deltas"]
    assert len(deltas) == 1 and deltas[0]["status"] == "open"
    changes = {row["change"] for row in deltas[0]["items"]}
    assert changes == {"new"}
    names = {row["proposed"].get("name") for row in deltas[0]["items"]}
    assert names == {"Gamma"}, "unchanged items never re-enter as delta noise"


def test_explicit_begin_review_freezes_without_a_verdict(runtime):
    graph = runtime.graph
    _plant_candidates(graph, "Alpha")
    compose_deterministic_draft_fn(graph)
    head = current_setup_draft_fn(graph)
    begun = begin_setup_review_fn(graph, head.id)
    assert begun["ok"] and begun["frozen"]
    assert begin_setup_review_fn(graph, head.id)["frozen"], "idempotent"

    _plant_candidates(graph, "Beta")
    result = compose_deterministic_draft_fn(graph)
    assert result.get("delta_id")
    assert current_setup_draft_fn(graph).id == head.id


def test_changed_and_conflicting_material_is_a_diff_not_a_replacement(runtime):
    graph = runtime.graph
    _plant_candidates(graph, "Alpha", "Beta")
    compose_deterministic_draft_fn(graph)
    items = project_setup_draft_fn(graph)["items"]
    alpha = next(i for i in items if i["proposed"].get("name") == "Alpha")
    review_setup_item_fn(graph, alpha["id"], "accept")

    # The same semantic projects return with different descriptions.
    for candidate in graph.objects(type="project_candidate"):
        graph.patch_object(candidate.id, {
            "description": f"richer description of {candidate.data['name']}",
        })
    result = compose_deterministic_draft_fn(graph)
    assert result.get("delta_id")
    delta = project_setup_draft_fn(graph)["deltas"][0]
    by_name = {row["proposed"].get("name"): row for row in delta["items"]}
    assert by_name["Alpha"]["change"] == "conflicting", \
        "the system now disagrees with something the owner accepted"
    assert by_name["Alpha"]["predecessor_item_id"] == alpha["id"]
    assert by_name["Beta"]["change"] == "changed"


def test_delta_apply_dismiss_defer(runtime):
    graph = runtime.graph
    _plant_candidates(graph, "Alpha")
    compose_deterministic_draft_fn(graph)
    head = current_setup_draft_fn(graph)
    begin_setup_review_fn(graph, head.id)
    _plant_candidates(graph, "Beta", "Gamma")
    compose_deterministic_draft_fn(graph)
    delta = project_setup_draft_fn(graph)["deltas"][0]

    deferred = dismiss_understanding_delta_fn(
        graph, delta["id"], verdict="defer",
    )
    assert deferred["status"] == "deferred"
    applied = apply_understanding_delta_fn(graph, delta["id"])
    assert applied["ok"] and applied["items_minted"] == 2
    projected = project_setup_draft_fn(graph)
    names = {i["proposed"].get("name") for i in projected["items"]
             if i["section"] == "projects"}
    assert {"Alpha", "Beta", "Gamma"} <= names
    minted = [i for i in projected["items"]
              if i["proposed"].get("name") in ("Beta", "Gamma")]
    assert all(i["status"] == "proposed" for i in minted), \
        "delta items join review as fresh proposals, never pre-decided"
    assert all(i["delta_ref"] for i in minted)
    # Project delta items carry a candidate so submission can promote them.
    assert all(i["candidate_ref"] for i in minted)
    assert projected["deltas"][0]["status"] == "applied"
    again = apply_understanding_delta_fn(graph, delta["id"])
    assert again.get("already_resolved")

    # Dismissal is durable on a second delta.
    _plant_candidates(graph, "Delta4")
    compose_deterministic_draft_fn(graph)
    second = next(d for d in project_setup_draft_fn(graph)["deltas"]
                  if d["status"] == "open")
    dismissed = dismiss_understanding_delta_fn(graph, second["id"], verdict="dismiss")
    assert dismissed["status"] == "dismissed"


# ---- the typed correction grammar ----------------------------------------------

def test_reject_carries_a_typed_correction_reason(runtime):
    graph = runtime.graph
    _plant_candidates(graph, "Alpha")
    compose_deterministic_draft_fn(graph)
    item = project_setup_draft_fn(graph)["items"][0]
    with pytest.raises(ValueError, match="correction must be one of"):
        review_setup_item_fn(graph, item["id"], "reject", correction="vibes")
    result = review_setup_item_fn(
        graph, item["id"], "reject", correction="duplicate",
    )
    assert result["ok"]
    projected = next(i for i in project_setup_draft_fn(graph)["items"]
                     if i["id"] == item["id"])
    assert projected["status"] == "rejected"
    assert projected["correction"] == "duplicate"


def test_owner_comment_is_durable_owner_evidence(runtime):
    graph = runtime.graph
    _plant_candidates(graph, "Alpha")
    compose_deterministic_draft_fn(graph)
    item = project_setup_draft_fn(graph)["items"][0]
    commented = comment_setup_item_fn(
        graph, item["id"], "this is really the fund, not a project",
    )
    assert commented["ok"]
    projected = next(i for i in project_setup_draft_fn(graph)["items"]
                     if i["id"] == item["id"])
    assert len(projected["comments"]) == 1
    row = projected["comments"][0]
    assert row["text"] == "this is really the fund, not a project"
    assert row["actor"] == "owner"
    # Commenting alone never settles the item's verdict.
    assert projected["status"] == "proposed"
    # And commenting freezes review like any owner decision.
    _plant_candidates(graph, "Beta")
    assert compose_deterministic_draft_fn(graph).get("delta_id")


def test_split_produces_fresh_items_and_supersedes_the_original(runtime):
    graph = runtime.graph
    _plant_candidates(graph, "Portfolio and Advising")
    compose_deterministic_draft_fn(graph)
    item = next(i for i in project_setup_draft_fn(graph)["items"]
                if i["section"] == "projects")
    with pytest.raises(ValueError, match="at least two"):
        split_setup_project_item_fn(graph, item["id"], [{"name": "Solo"}])
    result = split_setup_project_item_fn(graph, item["id"], [
        {"name": "Portfolio Support", "description": "companies I back"},
        {"name": "Advising", "description": "hands-on advisory work"},
    ])
    assert result["ok"] and result["created"] == 2
    projected = project_setup_draft_fn(graph)
    original = next(i for i in projected["items"] if i["id"] == item["id"])
    assert original["status"] == "superseded"
    parts = [i for i in projected["items"]
             if i["proposed"].get("name") in ("Portfolio Support", "Advising")]
    assert len(parts) == 2
    assert all(i["status"] == "proposed" for i in parts), \
        "split halves are fresh proposals; no verdict carries over"
    assert all(i["candidate_ref"] for i in parts)
    assert all(item["id"] in (i.get("split_from") or "") for i in parts)
