"""Gate B (onboarding product-closure): the synthesis convergence rules.

The 2026-07-14 live keyed run recomposed one draft 28 times: every
synthesis minted/patched project candidates, candidates re-versioned the
working understanding, the "organization" change re-scheduled synthesis,
and 24 near-identical update banners piled onto a frozen review. These
tests pin every cut in that circuit:

- synthesis-authored entries never schedule reinterpretation;
- model naming/confidence variance cannot move the working hash by itself;
- a draft request records its input fingerprint and is refused while the
  head already consumed the same horizon;
- one cumulative open delta per snapshot — new deltas supersede priors;
- applying a delta supersedes same-key predecessors instead of duplicating
  items, carrying the owner's decision state;
- budget pauses mint an answerable owner question and floor completion
  moves are budget-exempt;
- review resolution settles the campaign terminally;
- access proposals must be actionable strategies, not label inventories.
"""

from __future__ import annotations

import pytest

from activegraph import Graph, Runtime

from packs.llm_provider import clear_llm_provider
from packs.subject_profile import pack as subject_profile_pack
from packs.subject_synthesis import pack as subject_synthesis_pack
from packs.subject_synthesis.coordinator import (
    current_campaign_fn,
    open_comprehension_campaign_fn,
    propose_next_move_deterministic_fn,
    record_coordinator_move_fn,
    validate_coordinator_move_fn,
)
from packs.subject_synthesis.draft import (
    apply_understanding_delta_fn,
    begin_setup_review_fn,
    compose_deterministic_draft_fn,
    consumed_input_fingerprint_fn,
    current_setup_draft_fn,
    defer_setup_draft_fn,
    dismiss_overlap_cluster_fn,
    possible_overlap_clusters_fn,
    project_setup_draft_fn,
    project_understanding_deltas_fn,
    request_setup_draft_fn,
    review_setup_item_fn,
    review_setup_items_fn,
    semantic_item_key,
    synthesis_input_fingerprint_fn,
    _access_row_is_actionable,
    _mint_understanding_delta,
)
from packs.subject_synthesis.working import (
    compose_working_understanding_fn,
    contribute_source_lens_fn,
    pending_reinterpretations_fn,
)


@pytest.fixture
def runtime():
    rt = Runtime(Graph())
    rt.load_pack(subject_profile_pack)
    rt.load_pack(subject_synthesis_pack)
    rt.run_until_idle()
    return rt


@pytest.fixture(autouse=True)
def _clean_providers():
    clear_llm_provider()
    yield
    clear_llm_provider()


def _candidate(graph, name: str, *, kind: str = "synthesized",
               score: int = 800, sources=None):
    return graph.add_object("project_candidate", {
        "candidate_identity": f"cand:{kind}:{name.lower()}",
        "name": name, "kind": kind, "score_milli": score,
        "sources": list(sources or ["evidence:x"]),
        "rationale": "test material", "status": "proposed",
        "description": "", "project_id": None, "metadata": {},
    })


def _feed_lens(graph, *entries: str, affordance="web_research_understanding"):
    contribute_source_lens_fn(
        graph, affordance_id=affordance,
        source_surface_id=f"{affordance}:owner",
        contributions=[
            {"kind": "hypothesis", "statement": statement,
             "support_refs": [f"run:{statement[:8]}"], "confidence": 0.7,
             "entry_key": f"k:{statement[:24]}"}
            for statement in entries
        ],
    )


# ---- the loop circuit, cut ---------------------------------------------------

def test_synthesis_authored_candidates_never_schedule_reinterpretation(runtime):
    """THE live-run loop regression: a synthesized candidate appearing (or
    churning) between composes versions the packet but schedules NOTHING."""
    graph = runtime.graph
    _feed_lens(graph, "the owner runs a venture fund")
    first = compose_working_understanding_fn(graph)
    assert first["created"]
    for row in pending_reinterpretations_fn(graph):
        graph.patch_object(row["request_ref"], {"status": "completed"})

    _candidate(graph, "Untapped Capital Fund I")  # synthesis-authored
    second = compose_working_understanding_fn(graph)
    assert second["created"], "a new candidate still versions the packet"
    assert pending_reinterpretations_fn(graph) == [], (
        "synthesis output scheduled another synthesis — the feedback loop"
    )

    # Source-derived candidates DO schedule (they are new external material).
    _candidate(graph, "Atlas", kind="fact_seeded")
    third = compose_working_understanding_fn(graph)
    assert third["created"]
    assert any(
        row["target_kind"] in ("synthesis", "draft_recompose")
        for row in pending_reinterpretations_fn(graph)
    )


def test_naming_variance_alone_cannot_move_the_working_hash(runtime):
    """Confidence/score churn on synthesis-authored candidates is invisible
    to the hash; only their stable identity counts."""
    graph = runtime.graph
    _feed_lens(graph, "the owner builds agent frameworks")
    candidate = _candidate(graph, "ActiveGraph AI", score=800)
    first = compose_working_understanding_fn(graph)
    assert first["created"]

    graph.patch_object(candidate.id, {"score_milli": 900})
    second = compose_working_understanding_fn(graph)
    assert second["created"] is False, (
        "synthesis-candidate score churn re-versioned the understanding"
    )


def test_draft_request_is_refused_over_a_consumed_input_horizon(runtime):
    graph = runtime.graph
    evidence = graph.add_object("owner_evidence", {
        "evidence_identity": "ev:1", "surface": "identity_seed",
        "text": "I run Untapped Capital", "metadata": {},
    })
    _candidate(graph, "Atlas", kind="fact_seeded", sources=[evidence.id])
    composed = compose_deterministic_draft_fn(graph)
    assert composed["ok"]
    head = current_setup_draft_fn(graph)
    stamped = (head.data.get("coverage") or {}).get("input_fingerprint")
    assert stamped == synthesis_input_fingerprint_fn(graph)
    assert consumed_input_fingerprint_fn(graph) == stamped

    refused = request_setup_draft_fn(graph)
    assert refused["request_id"] is None
    assert refused["skipped"] == "no_new_source_material"

    # New source material moves the fingerprint and re-opens the request.
    _candidate(graph, "Borealis", kind="label_seeded")
    allowed = request_setup_draft_fn(graph)
    assert allowed.get("created") and allowed["request_id"]


# ---- one cumulative update per snapshot ---------------------------------------

def _frozen_head_with_delta_rows(graph):
    _candidate(graph, "Atlas", kind="fact_seeded")
    compose_deterministic_draft_fn(graph)
    head = current_setup_draft_fn(graph)
    begin_setup_review_fn(graph, head.id, actor="owner:test")
    return head


def _rows(*names: str):
    return [
        {
            "section": "identity",
            "proposed": {"attribute": "role", "value": name},
            "rationale": "test", "evidence_refs": ["ref:1"],
            "confidence": 0.7, "uncertainty": "",
        }
        for name in names
    ]


def test_new_delta_supersedes_prior_open_deltas(runtime):
    graph = runtime.graph
    head = _frozen_head_with_delta_rows(graph)
    first = _mint_understanding_delta(
        graph, graph, head=head, rows=_rows("General Partner"),
        source="synthesis", run_id=None, coverage={},
    )
    second = _mint_understanding_delta(
        graph, graph, head=head,
        rows=_rows("General Partner", "Founder"),
        source="synthesis", run_id=None, coverage={},
    )
    assert second["superseded"] == 1
    deltas = project_understanding_deltas_fn(graph, draft_id=head.id)
    by_status = {}
    for row in deltas:
        by_status.setdefault(row["status"], []).append(row)
    assert len(by_status.get("open", [])) == 1, (
        f"expected ONE open cumulative delta, saw {len(by_status.get('open', []))}"
    )
    assert by_status["open"][0]["id"] == second["delta_id"]
    superseded = by_status.get("superseded", [])
    assert [row["id"] for row in superseded] == [first["delta_id"]]


def test_deferred_delta_stays_deferred_unless_genuinely_new(runtime):
    from packs.subject_synthesis.draft import dismiss_understanding_delta_fn

    graph = runtime.graph
    head = _frozen_head_with_delta_rows(graph)
    first = _mint_understanding_delta(
        graph, graph, head=head, rows=_rows("General Partner"),
        source="synthesis", run_id=None, coverage={},
    )
    dismiss_understanding_delta_fn(graph, first["delta_id"], verdict="defer")

    same = _mint_understanding_delta(
        graph, graph, head=head, rows=_rows("General Partner"),
        source="synthesis", run_id=None, coverage={},
    )
    assert same["status"] == "deferred", (
        "re-synthesis of already-deferred material must not nag again"
    )
    fresh = _mint_understanding_delta(
        graph, graph, head=head, rows=_rows("General Partner", "TED speaker"),
        source="synthesis", run_id=None, coverage={},
    )
    assert fresh["status"] == "open", "genuinely new keys earn ONE notification"


def test_apply_supersedes_same_key_predecessor_and_keeps_decisions(runtime):
    graph = runtime.graph
    head = _frozen_head_with_delta_rows(graph)
    projection = project_setup_draft_fn(graph)
    atlas = next(
        row for row in projection["items"] if row["section"] == "projects"
    )
    review_setup_item_fn(graph, atlas["id"], "accept", actor="owner:test")

    delta = _mint_understanding_delta(
        graph, graph, head=head, rows=[{
            "section": "projects",
            "proposed": {"name": "Atlas",
                         "description": "a refreshed, richer description"},
            "rationale": "newer synthesis", "evidence_refs": ["ref:2"],
            "confidence": 0.8, "uncertainty": "",
        }],
        source="synthesis", run_id=None, coverage={},
    )
    applied = apply_understanding_delta_fn(graph, delta["delta_id"], actor="owner:test")
    assert applied["items_minted"] == 1 and applied["superseded"] == 1

    projection = project_setup_draft_fn(graph)
    atlas_rows = [
        row for row in projection["items"]
        if semantic_item_key(row["section"],
                             dict(row.get("edited_value") or row["proposed"]))
        == semantic_item_key("projects", {"name": "Atlas"})
    ]
    assert len(atlas_rows) == 1, "apply minted a duplicate active item"
    assert atlas_rows[0]["status"] == "accepted", (
        "the owner's decision did not travel onto the refreshed content"
    )
    assert atlas_rows[0]["proposed"]["description"] == "a refreshed, richer description"


def test_apply_never_overrides_an_owner_edit(runtime):
    graph = runtime.graph
    head = _frozen_head_with_delta_rows(graph)
    projection = project_setup_draft_fn(graph)
    atlas = next(row for row in projection["items"] if row["section"] == "projects")
    review_setup_item_fn(
        graph, atlas["id"], "edit", actor="owner:test",
        edited_value={"description": "the owner's own words"},
    )
    delta = _mint_understanding_delta(
        graph, graph, head=head, rows=[{
            "section": "projects",
            "proposed": {"name": "Atlas", "description": "model rewrite"},
            "rationale": "newer synthesis", "evidence_refs": ["ref:2"],
            "confidence": 0.8, "uncertainty": "",
        }],
        source="synthesis", run_id=None, coverage={},
    )
    applied = apply_understanding_delta_fn(graph, delta["delta_id"], actor="owner:test")
    assert applied["skipped_owner_edited"] == 1 and applied["items_minted"] == 0


# ---- possible-overlap clusters -------------------------------------------------

def test_possible_overlap_clusters_flag_but_never_merge(runtime):
    graph = runtime.graph
    _candidate(graph, "Untapped Capital Fund I", kind="fact_seeded")
    _candidate(graph, "Untapped Capital VC operations", kind="fact_seeded")
    _candidate(graph, "Pippin Framework", kind="fact_seeded")
    compose_deterministic_draft_fn(graph)
    head = current_setup_draft_fn(graph)

    clusters = possible_overlap_clusters_fn(graph, draft_id=head.id)
    assert len(clusters) == 1
    names = {row["name"] for row in clusters[0]["items"]}
    assert names == {"Untapped Capital Fund I", "Untapped Capital VC operations"}
    assert "untapped" in clusters[0]["shared_tokens"]
    assert clusters[0]["why"]

    # Both items remain active — flagging is a question, never a merge.
    projection = project_setup_draft_fn(graph)
    active = [row for row in projection["items"] if row["section"] == "projects"]
    assert len(active) == 3

    # "Keep separate" is durable.
    dismissed = dismiss_overlap_cluster_fn(
        graph, head.id, clusters[0]["cluster_key"], actor="owner:test",
    )
    assert dismissed["ok"]
    assert possible_overlap_clusters_fn(graph, draft_id=head.id) == []


# ---- batch review ---------------------------------------------------------------

def test_batch_review_settles_every_item_in_one_call(runtime):
    graph = runtime.graph
    _candidate(graph, "Atlas", kind="fact_seeded")
    _candidate(graph, "Borealis", kind="fact_seeded")
    compose_deterministic_draft_fn(graph)
    projection = project_setup_draft_fn(graph)
    ids = [row["id"] for row in projection["items"] if row["section"] == "projects"]
    assert len(ids) == 2
    result = review_setup_items_fn(graph, ids, "accept", actor="owner:test")
    assert result["ok"] and result["settled"] == 2
    projection = project_setup_draft_fn(graph)
    assert all(
        row["status"] == "accepted"
        for row in projection["items"] if row["id"] in ids
    )


# ---- budgets and terminal settlement --------------------------------------------

def test_budget_pause_mints_an_answerable_question(runtime):
    graph = runtime.graph
    open_comprehension_campaign_fn(graph, selected_affordances=[])
    campaign = current_campaign_fn(graph)
    graph.patch_object(campaign.id, {
        "budgets": {**dict(campaign.data.get("budgets") or {}), "max_moves": 0},
    })
    campaign = current_campaign_fn(graph)
    recorded = record_coordinator_move_fn(graph, campaign.id, {
        "kind": "align_entities", "params": {},
        "rationale": "any budgeted move",
    }, proposer={"kind": "model"})
    assert recorded["verdict"] == "pause_owner"
    assert "move_budget_exhausted" in recorded["reasons"]
    questions = [
        obj for obj in graph.objects(type="owner_question")
        if obj.data.get("status") == "open"
    ]
    assert len(questions) == 1, "a budget pause with nothing to answer is a deadlock"
    option_ids = {row["id"] for row in questions[0].data.get("options") or []}
    assert option_ids == {"wrap_up", "extend_budget"}


def test_floor_completion_moves_are_budget_exempt(runtime):
    graph = runtime.graph
    open_comprehension_campaign_fn(graph, selected_affordances=[])
    campaign = current_campaign_fn(graph)
    graph.patch_object(campaign.id, {
        "budgets": {**dict(campaign.data.get("budgets") or {}), "max_moves": 0},
    })
    campaign = current_campaign_fn(graph)
    move = propose_next_move_deterministic_fn(graph, campaign)
    assert move is not None and move["kind"] == "synthesize"
    verdict = validate_coordinator_move_fn(
        graph, campaign, move, proposer={"kind": "deterministic"},
    )
    assert verdict["verdict"] == "execute", (
        "the deterministic floor must always be able to finish the campaign"
    )
    # The same move from a MODEL proposer stays budget-bound.
    verdict = validate_coordinator_move_fn(
        graph, campaign, move, proposer={"kind": "model"},
    )
    assert verdict["verdict"] == "pause_owner"


def test_review_resolution_settles_the_campaign_terminally(runtime):
    graph = runtime.graph
    open_comprehension_campaign_fn(graph, selected_affordances=[])
    campaign = current_campaign_fn(graph)
    graph.patch_object(campaign.id, {"status": "paused_owner"})
    _candidate(graph, "Atlas", kind="fact_seeded")
    compose_deterministic_draft_fn(graph)
    head = current_setup_draft_fn(graph)
    deferred = defer_setup_draft_fn(graph, head.id, actor="owner:test")
    assert deferred["ok"]
    campaign = current_campaign_fn(graph)
    assert campaign.data["status"] == "completed", (
        "a resolved review left the campaign parked on an ownerless pause"
    )
    assert campaign.data.get("stop_reason") == "review_ready"


# ---- access quality --------------------------------------------------------------

def test_access_rows_must_be_actionable_strategies():
    assert _access_row_is_actionable(
        "current LP conversations",
        "search Gmail's Limited Partner label and recent sent threads",
        "LP threads cluster under this label",
    )
    # A raw label inventory fails whatever else it carries.
    assert not _access_row_is_actionable(
        "vc operations",
        "Labels: 'Dealflow', 'Startup Founder', 'Limited Partner'",
        "labels exist",
    )
    # Missing the question or the reason fails.
    assert not _access_row_is_actionable(
        "", "search for ActiveGraph threads", "useful",
    )
    assert not _access_row_is_actionable(
        "ActiveGraph decisions", "search for ActiveGraph threads", "",
    )
