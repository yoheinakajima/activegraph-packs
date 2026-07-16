"""ADR 0051: review horizons are accepted explicitly.

Conformance for the closed delta-disposition vocabulary, the atomic
submission re-check (a stale client can never submit around an open
cumulative update), the recorded accepted horizon, the successor review
batch for post-acceptance material, legacy contradictory-store recovery,
and the project evidence-qualification gate (topology corroborates, never
proposes).
"""

from __future__ import annotations

import hashlib

import pytest

from activegraph import Graph, Runtime

from packs.activity_normalizer import pack as normalizer_pack
from packs.connector_control import pack as connector_control_pack
from packs.projects import pack as projects_pack
from packs.subject_profile import pack as subject_profile_pack
from packs.subject_synthesis import pack as subject_synthesis_pack
from packs.tool_gateway import pack as tool_gateway_pack
from packs.subject_synthesis.draft import (
    apply_understanding_delta_fn,
    begin_setup_draft_submission_fn,
    begin_setup_review_fn,
    commit_setup_draft_fn,
    complete_setup_draft_submission_fn,
    compose_deterministic_draft_fn,
    current_setup_draft_fn,
    dismiss_understanding_delta_fn,
    open_understanding_deltas_fn,
    possible_overlap_clusters_fn,
    prepare_setup_draft_fn,
    project_setup_draft_fn,
    project_understanding_deltas_fn,
    request_setup_draft_fn,
    review_setup_item_fn,
    _mint_understanding_delta,
)


@pytest.fixture
def runtime():
    rt = Runtime(Graph())
    rt.load_pack(normalizer_pack)
    rt.load_pack(connector_control_pack)
    rt.load_pack(tool_gateway_pack)
    rt.load_pack(subject_profile_pack)
    rt.load_pack(projects_pack)
    rt.load_pack(subject_synthesis_pack)
    rt.run_until_idle()
    return rt


def _owner_evidence(graph, runtime):
    text = "I am Yohei Nakajima, GP at Untapped Capital."
    digest = hashlib.sha256(text.encode()).hexdigest()
    item = graph.add_object("acquired_item", {
        "source_surface_id": "identity_seed", "provider_item_id": "seed-1",
        "dedup_key": "seed-1", "source_ref": "seed", "source_hash": digest,
        "provider_time": None, "replay_mode": "inline",
        "replay_payload_ref": text, "replay_payload_hash": digest,
        "media_type": "text/plain", "importer_id": "test", "importer_version": "1",
    })
    graph.add_object("acquired_content", {
        "acquired_item_id": item.id, "normalized_content": text,
        "normalized_metadata": {"subject_scope": "owner_profile"},
        "source_category": "local_knowledge", "connection_path": "manual",
        "is_fixture": True,
    })
    runtime.run_until_idle()
    return graph.objects(type="activity_evidence")[0]


def _fact(graph, evidence, attribute, value):
    return graph.add_object("subject_fact", {
        "fact_identity": f"fact-{attribute}-{value}",
        "subject_ref": "owner", "attribute": attribute, "value": value,
        "text": value, "status": "promoted", "confidence": 0.9, "trust": 0.9,
        "candidate_id": None, "annotation_id": None, "evidence_id": evidence.id,
        "source_surface_id": "identity_seed", "verdict_id": None,
        "supersedes_fact_id": None, "metadata": {},
    })


def _leaf(graph, ref="leaf-1"):
    request = graph.add_object("comprehension_request", {
        "request_identity": f"req-{ref}", "recipe_id": "gmail_sent_v1",
        "service": "gmail", "source_surface_id": "gmail:owner",
        "plan_identity": "", "status": "completed", "requested_by": "test",
        "counts": {"items": 1, "batches": 1, "batches_done": 1, "leaves": 1},
        "coverage": {"selected": 1}, "item_refs": ["m1"], "metadata": {},
    })
    return graph.add_object("source_item_summary", {
        "summary_identity": ref, "request_id": request.id,
        "recipe_id": "gmail_sent_v1", "item_ref": "m1",
        "evidence_refs": ["evidence-m1"], "batch_index": 0,
        "fields": {
            "authored_intent": "coordinating the Atlas beta launch",
            "projects": ["Atlas"], "topics": ["launch"],
            "people": [{"name": "Jane Doe", "relationship": "collaborator"}],
        },
        "model": "fast-test", "injection_flags": [], "metadata": {},
    })


def _integration_profile(graph):
    return graph.add_object("integration_profile", {
        "profile_identity": "gmail:owner", "profile_version": 1,
        "service": "gmail", "account_ref": "acct-1", "status": "active",
        "signal_map": [
            {"surface": "label:Dealflow", "candidate_types": ["task_candidate"],
             "estimated_richness": "unmeasured"},
        ],
        "metadata": {},
    })


def _delta_rows(*names, section="identity"):
    return [
        {
            "section": section,
            "proposed": {"attribute": "role", "value": name},
            "rationale": "late material", "evidence_refs": ["ref:1"],
            "confidence": 0.7, "uncertainty": "",
        }
        for name in names
    ]


def _floor_draft(graph, runtime):
    """A reviewable one-item draft through the deterministic floor."""
    graph.add_object("profile_candidate", {
        "candidate_identity": "cand-name", "text": "name: Yohei",
        "confidence": 0.9, "evidence_id": "", "evidence_identity": "",
        "revision_id": "", "extraction_record_id": "x", "extractor_id": "t",
        "extractor_version": "1", "extraction_config_id": "t@1",
        "status": "candidate", "invalidation_reason": None,
        "metadata": {}, "attribute": "name", "value": "Yohei Nakajima",
    })
    compose_deterministic_draft_fn(graph)
    runtime.run_until_idle()
    return current_setup_draft_fn(graph)


# ---- 12.1 review/delta semantics ---------------------------------------------


def test_frozen_draft_plus_open_delta_rejects_submission(runtime):
    """(1)+(6): a delta that won event ordering blocks a later submission
    with an owner-language conflict and a route back to Review."""
    graph = runtime.graph
    head = _floor_draft(graph, runtime)
    begin_setup_review_fn(graph, head.id, actor="owner:test")
    item = project_setup_draft_fn(graph)["items"][0]
    review_setup_item_fn(graph, item["id"], "accept")
    minted = _mint_understanding_delta(
        graph, graph, head=head, rows=_delta_rows("General Partner"),
        source="synthesis", run_id=None, coverage={},
    )
    assert minted["status"] == "open"
    refused = begin_setup_draft_submission_fn(graph, head.id)
    assert refused["ok"] is False
    assert refused["reason"] == "open_understanding_update"
    assert refused["route"] == "setup_review"
    assert "arrived before setup was saved" in refused["conflict"]
    assert graph.get_object(head.id).data["status"] == "proposed"


def test_applied_delta_rows_join_review_undecided(runtime):
    """(2): applying stages rows as UNDECIDED proposals — never verdicts."""
    graph = runtime.graph
    head = _floor_draft(graph, runtime)
    begin_setup_review_fn(graph, head.id, actor="owner:test")
    minted = _mint_understanding_delta(
        graph, graph, head=head, rows=_delta_rows("General Partner"),
        source="synthesis", run_id=None, coverage={},
    )
    applied = apply_understanding_delta_fn(graph, minted["delta_id"])
    assert applied["ok"] and applied["items_minted"] == 1
    projection = project_setup_draft_fn(graph)
    staged = [
        row for row in projection["items"]
        if row["proposed"].get("value") == "General Partner"
    ]
    assert staged and staged[0]["status"] == "proposed"
    assert staged[0]["verdict"] is None
    # Staged-but-undecided items block a bare submission.
    refused = begin_setup_draft_submission_fn(graph, head.id)
    assert refused["ok"] is False
    assert refused["reason"] == "undecided_items"


def test_dismissed_delta_permits_submission(runtime):
    """(4): an explicit "not needed" clears the block."""
    graph = runtime.graph
    head = _floor_draft(graph, runtime)
    begin_setup_review_fn(graph, head.id, actor="owner:test")
    item = project_setup_draft_fn(graph)["items"][0]
    review_setup_item_fn(graph, item["id"], "accept")
    minted = _mint_understanding_delta(
        graph, graph, head=head, rows=_delta_rows("General Partner"),
        source="synthesis", run_id=None, coverage={},
    )
    dismiss_understanding_delta_fn(graph, minted["delta_id"], verdict="dismiss")
    begun = begin_setup_draft_submission_fn(graph, head.id)
    assert begun["ok"] is True
    dispositions = graph.get_object(head.id).data["metadata"]["delta_dispositions"]
    assert dispositions[minted["delta_id"]] == "dismissed"


def test_deferred_delta_permits_submission_and_stays_visible(runtime):
    """(5): explicit deferral permits submission; the deferred work remains
    a visible, unresolved row — never counted reviewed."""
    graph = runtime.graph
    head = _floor_draft(graph, runtime)
    begin_setup_review_fn(graph, head.id, actor="owner:test")
    item = project_setup_draft_fn(graph)["items"][0]
    review_setup_item_fn(graph, item["id"], "accept")
    minted = _mint_understanding_delta(
        graph, graph, head=head, rows=_delta_rows("General Partner"),
        source="synthesis", run_id=None, coverage={},
    )
    dismiss_understanding_delta_fn(graph, minted["delta_id"], verdict="defer")
    begun = begin_setup_draft_submission_fn(graph, head.id)
    assert begun["ok"] is True
    runtime.run_until_idle()
    completed = complete_setup_draft_submission_fn(graph, head.id)
    assert completed["status"] == "submitted"
    rows = project_understanding_deltas_fn(graph, draft_id=head.id)
    deferred = [row for row in rows if row["status"] == "deferred"]
    assert len(deferred) == 1 and len(deferred[0]["items"]) == 1
    dispositions = graph.get_object(head.id).data["metadata"]["delta_dispositions"]
    assert dispositions[minted["delta_id"]] == "deferred"
    # A deferred delta stays actionable later: applying it against the now
    # accepted head opens a successor review, not a dead letter.
    applied = apply_understanding_delta_fn(graph, minted["delta_id"])
    assert applied["ok"] and applied["successor_draft_id"]


def test_post_acceptance_delta_becomes_successor_review(runtime):
    """(7)+(9): a genuinely later delta targets the accepted head, surfaces
    as an open update, and applies into a successor review batch without
    touching the predecessor."""
    graph = runtime.graph
    head = _floor_draft(graph, runtime)
    begin_setup_review_fn(graph, head.id, actor="owner:test")
    item = project_setup_draft_fn(graph)["items"][0]
    review_setup_item_fn(graph, item["id"], "accept")
    assert begin_setup_draft_submission_fn(graph, head.id)["ok"]
    runtime.run_until_idle()
    assert complete_setup_draft_submission_fn(graph, head.id)["status"] == "submitted"

    minted = _mint_understanding_delta(
        graph, graph, head=head, rows=_delta_rows("General Partner"),
        source="synthesis", run_id=None, coverage={},
    )
    assert minted["status"] == "open"
    assert [obj.id for obj in open_understanding_deltas_fn(graph, draft_id=head.id)]

    applied = apply_understanding_delta_fn(graph, minted["delta_id"], actor="owner:test")
    assert applied["ok"] is True
    successor_id = applied["successor_draft_id"]
    assert applied["review_context"] == "workspace"
    successor = graph.get_object(successor_id)
    assert successor.data["source"] == "successor"
    assert successor.data["status"] == "proposed"
    assert successor.data["metadata"]["successor_of"] == head.id
    assert successor.data["metadata"]["review_started"]
    # The predecessor is untouched immutable history.
    assert graph.get_object(head.id).data["status"] == "submitted"
    # Only the delta's rows appear, undecided.
    projection = project_setup_draft_fn(graph)
    assert projection["draft"]["id"] == successor_id
    assert [row["status"] for row in projection["items"]] == ["proposed"]
    # Promotion still requires an explicit verdict + canonical submission.
    review_setup_item_fn(graph, projection["items"][0]["id"], "accept")
    assert begin_setup_draft_submission_fn(graph, successor_id)["ok"]
    runtime.run_until_idle()
    assert complete_setup_draft_submission_fn(
        graph, successor_id
    )["status"] == "submitted"


def test_successor_promotion_is_replay_stable(runtime):
    """(8): re-applying the same delta returns the SAME successor review;
    the accepted horizon on the predecessor never moves."""
    graph = runtime.graph
    head = _floor_draft(graph, runtime)
    begin_setup_review_fn(graph, head.id, actor="owner:test")
    item = project_setup_draft_fn(graph)["items"][0]
    review_setup_item_fn(graph, item["id"], "accept")
    assert begin_setup_draft_submission_fn(graph, head.id)["ok"]
    runtime.run_until_idle()
    complete_setup_draft_submission_fn(graph, head.id)
    horizon = graph.get_object(head.id).data["metadata"]["accepted_horizon"]
    minted = _mint_understanding_delta(
        graph, graph, head=head, rows=_delta_rows("General Partner"),
        source="synthesis", run_id=None, coverage={},
    )
    first = apply_understanding_delta_fn(graph, minted["delta_id"])
    second = apply_understanding_delta_fn(graph, minted["delta_id"])
    assert first["successor_draft_id"] == second["successor_draft_id"]
    assert second.get("already_resolved") is True
    assert graph.get_object(head.id).data["metadata"]["accepted_horizon"] == horizon
    drafts = [
        obj for obj in graph.objects(type="setup_draft")
        if obj.data.get("source") == "successor"
    ]
    assert len(drafts) == 1


def test_unchanged_and_owner_edited_keys_never_reask_in_successor(runtime):
    """(3): prior verdicts and owner edits survive — the successor holds
    only genuinely new or changed rows."""
    graph = runtime.graph
    head = _floor_draft(graph, runtime)
    begin_setup_review_fn(graph, head.id, actor="owner:test")
    item = project_setup_draft_fn(graph)["items"][0]
    review_setup_item_fn(
        graph, item["id"], "edit",
        edited_value={"value": "Yohei N."},
    )
    assert begin_setup_draft_submission_fn(graph, head.id)["ok"]
    runtime.run_until_idle()
    complete_setup_draft_submission_fn(graph, head.id)
    # The delta repeats the owner-edited key plus one new row.
    edited_row = {
        "section": "identity",
        "proposed": {"attribute": "name", "value": "Yohei N."},
        "rationale": "same key as the owner edit", "evidence_refs": ["ref:1"],
        "confidence": 0.7, "uncertainty": "",
    }
    minted = _mint_understanding_delta(
        graph, graph, head=head,
        rows=[edited_row, *_delta_rows("General Partner")],
        source="synthesis", run_id=None, coverage={},
    )
    applied = apply_understanding_delta_fn(graph, minted["delta_id"])
    assert applied["ok"]
    successor = graph.get_object(applied["successor_draft_id"])
    items = [
        obj for obj in graph.objects(type="setup_draft_item")
        if obj.data.get("draft_id") == successor.id
    ]
    values = {obj.data["proposed"].get("value") for obj in items}
    assert values == {"General Partner"}, (
        "the owner-edited key must not re-ask; only new material appears"
    )


def test_legacy_submitted_draft_with_open_delta_recovers(runtime):
    """(10): the preserved owner-store shape — submitted draft + open delta
    minted BEFORE submission (no dispositions recorded) — recovers into a
    successor review without any history rewrite."""
    graph = runtime.graph
    head = _floor_draft(graph, runtime)
    begin_setup_review_fn(graph, head.id, actor="owner:test")
    minted = _mint_understanding_delta(
        graph, graph, head=head,
        rows=_delta_rows("General Partner", "Founder"),
        source="synthesis", run_id=None, coverage={},
    )
    item = project_setup_draft_fn(graph)["items"][0]
    review_setup_item_fn(graph, item["id"], "accept")
    # Legacy stores submitted WITHOUT the re-check: reproduce that exact
    # shape by resolving the draft directly (history, not the new API).
    graph.patch_object(head.id, {"status": "submitted"},
                       rationale="legacy submission without horizon re-check")
    assert graph.get_object(minted["delta_id"]).data["status"] == "open"

    applied = apply_understanding_delta_fn(graph, minted["delta_id"])
    assert applied["ok"] is True
    assert applied["items_minted"] == 2
    assert graph.get_object(head.id).data["status"] == "submitted"
    assert graph.get_object(minted["delta_id"]).data["status"] == "applied"
    successor = graph.get_object(applied["successor_draft_id"])
    assert successor.data["metadata"]["review_context"] == "workspace"


def test_new_cumulative_delta_supersedes_only_unresolved_and_keeps_keys(runtime):
    """(11): supersession folds unresolved predecessors' still-relevant keys
    into the newest cumulative row; resolved deltas stay resolved."""
    graph = runtime.graph
    head = _floor_draft(graph, runtime)
    begin_setup_review_fn(graph, head.id, actor="owner:test")
    first = _mint_understanding_delta(
        graph, graph, head=head, rows=_delta_rows("General Partner"),
        source="synthesis", run_id=None, coverage={},
    )
    dismissed = _mint_understanding_delta(
        graph, graph, head=head, rows=_delta_rows("Founder"),
        source="synthesis", run_id=None, coverage={},
    )
    # The second delta superseded the first and carried its key.
    assert dismissed["superseded"] == 1
    dismiss_understanding_delta_fn(graph, dismissed["delta_id"], verdict="dismiss")
    third = _mint_understanding_delta(
        graph, graph, head=head, rows=_delta_rows("Advisor"),
        source="synthesis", run_id=None, coverage={},
    )
    rows = {row["status"]: row for row in
            project_understanding_deltas_fn(graph, draft_id=head.id)}
    assert rows["dismissed"]["id"] == dismissed["delta_id"]
    open_row = rows["open"]
    assert open_row["id"] == third["delta_id"]
    # The dismissed predecessor was NOT resurrected; only the new key opens.
    values = {item["proposed"]["value"] for item in open_row["items"]}
    assert values == {"Advisor"}


# ---- 12.2 project candidate quality --------------------------------------------


def _keyed_commit(runtime, projects_section, extra_setup=None):
    graph = runtime.graph
    evidence = _owner_evidence(graph, runtime)
    _fact(graph, evidence, "name", "Yohei Nakajima")
    _leaf(graph)
    profile = _integration_profile(graph)
    if extra_setup:
        extra_setup(graph)
    request = request_setup_draft_fn(graph)
    payload = prepare_setup_draft_fn(graph, request["request_id"])
    leaf_ref = payload["comprehension"][0]["ref"]
    outcome = {
        "ok": True, "model": "reasoning-test",
        "sections": {
            "identity": [], "narrative": [], "instructions": [],
            "people": [], "access": [],
            "projects": projects_section(payload, profile, leaf_ref),
        },
        "response_sample": "{}", "response_length": 10, "error": None,
    }
    committed = commit_setup_draft_fn(
        graph, request["request_id"], payload, outcome,
    )
    runtime.run_until_idle()
    return graph, committed


def test_topology_only_evidence_cannot_propose_a_project(runtime):
    """(1)+(4): an integration profile alone proposes nothing; the drop is
    counted, never silent."""
    def section(payload, profile, leaf_ref):
        return [{
            "name": "Venture Capital Fund Operations",
            "description": "generic function derived from labels",
            "refs": [profile.id], "rationale": "labels suggest it",
        }]

    graph, committed = _keyed_commit(runtime, section)
    assert committed["ok"]
    items = [
        obj for obj in graph.objects(type="setup_draft_item")
        if obj.data.get("section") == "projects"
    ]
    assert items == []
    assert not [
        obj for obj in graph.objects(type="project_candidate")
        if obj.data.get("name") == "Venture Capital Fund Operations"
    ]
    run = graph.objects(type="subject_synthesis_run")[-1]
    assert run.data["proposed"]["dropped_topology_only"] == 1


def test_label_corroborates_an_independently_supported_project(runtime):
    """(2)+(3): comprehension evidence proposes; the label ref rides along
    as corroboration."""
    def section(payload, profile, leaf_ref):
        return [{
            "name": "Atlas",
            "description": "The beta launch coordinated in sent mail.",
            "refs": [leaf_ref, profile.id],
            "rationale": "dominant sent-mail thread; label corroborates",
        }]

    graph, committed = _keyed_commit(runtime, section)
    items = [
        obj for obj in graph.objects(type="setup_draft_item")
        if obj.data.get("section") == "projects"
    ]
    assert len(items) == 1
    candidate = graph.get_object(items[0].data["candidate_ref"])
    assert candidate.data["name"] == "Atlas"
    sources = set(candidate.data["sources"])
    assert any(str(ref).startswith("integration_profile") for ref in sources)
    assert any(not str(ref).startswith("integration_profile") for ref in sources)


def test_project_proposals_require_description_and_rationale(runtime):
    """(5): empty shells drop as low quality, counted on the run receipt."""
    def section(payload, profile, leaf_ref):
        return [
            {"name": "Shell Project", "description": "",
             "refs": [leaf_ref], "rationale": "cited but empty"},
            {"name": "No Rationale", "description": "has words",
             "refs": [leaf_ref], "rationale": ""},
            {"name": "Atlas", "description": "The beta launch you coordinate.",
             "refs": [leaf_ref], "rationale": "dominant thread",
             "uncertainty": "maybe the fund, maybe the product"},
        ]

    graph, committed = _keyed_commit(runtime, section)
    items = [
        obj for obj in graph.objects(type="setup_draft_item")
        if obj.data.get("section") == "projects"
    ]
    assert [obj.data["proposed"]["name"] for obj in items] == ["Atlas"]
    kept = items[0].data
    assert kept["proposed"]["description"]
    assert kept["rationale"]
    assert kept["evidence_refs"]
    assert kept["uncertainty"]
    run = graph.objects(type="subject_synthesis_run")[-1]
    assert run.data["proposed"]["dropped_low_quality"] >= 2


def test_overlapping_candidates_flag_but_never_auto_merge(runtime):
    """(6): the owner run's dealflow pair clusters for an owner call."""
    def section(payload, profile, leaf_ref):
        return [
            {"name": "Investment Dealflow Management",
             "description": "reviewing inbound startup dealflow",
             "refs": [leaf_ref], "rationale": "recurring sent-mail topic"},
            {"name": "Venture Capital Dealflow Management",
             "description": "the same recurring dealflow work",
             "refs": [leaf_ref], "rationale": "recurring sent-mail topic"},
        ]

    graph, committed = _keyed_commit(runtime, section)
    clusters = possible_overlap_clusters_fn(graph)
    assert len(clusters) == 1
    names = {row["name"] for row in clusters[0]["items"]}
    assert names == {
        "Investment Dealflow Management", "Venture Capital Dealflow Management",
    }
    # Nothing merged, nothing decided: both remain undecided proposals.
    items = [
        obj for obj in graph.objects(type="setup_draft_item")
        if obj.data.get("section") == "projects"
    ]
    assert sorted(obj.data["status"] for obj in items) == ["proposed", "proposed"]


def test_promotion_remains_canonical_through_the_projects_pack(runtime):
    """(7): accepting a project item still promotes through the projects
    pack's candidate → confirm pipeline, never a parallel store."""
    def section(payload, profile, leaf_ref):
        return [{
            "name": "Atlas", "description": "The beta launch you coordinate.",
            "refs": [leaf_ref], "rationale": "dominant thread",
        }]

    graph, committed = _keyed_commit(runtime, section)
    projection = project_setup_draft_fn(graph)
    item = next(
        row for row in projection["items"] if row["section"] == "projects"
    )
    review_setup_item_fn(graph, item["id"], "accept")
    assert begin_setup_draft_submission_fn(
        graph, projection["draft"]["id"]
    )["ok"]
    runtime.run_until_idle()
    completed = complete_setup_draft_submission_fn(
        graph, projection["draft"]["id"]
    )
    assert completed["status"] == "submitted"
    projects = [
        obj for obj in graph.objects(type="project")
        if obj.data.get("name") == "Atlas"
    ]
    assert len(projects) == 1
    assert projects[0].data["seeded_from_candidate_id"] == item["candidate_ref"]
