"""ADR 0047: comprehension is a governed agentic loop.

Covers the understanding-affordance contract (with a fixture-only third
affordance proving it is neither Gmail- nor web-search-shaped), source
lenses with support-vs-context lineage, the versioned working
understanding with authority classes and non-circular corroboration,
targeted reinterpretation, the deterministic move validator, the
zero-key deterministic proposer, owner-question pauses, and bounded
evidence drill-downs.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from activegraph import Graph, Runtime
from activegraph.llm import LLMResponse

from packs.llm_provider import (
    ResolvedLLMProvider,
    clear_llm_provider,
    set_llm_provider,
)
from packs.subject_profile import pack as subject_profile_pack
from packs.subject_synthesis import pack as subject_synthesis_pack
from packs.subject_synthesis.affordance import (
    affordance_catalog_fn,
    get_understanding_affordance,
    register_understanding_affordance,
    unregister_understanding_affordance,
    validate_understanding_affordance,
)
from packs.subject_synthesis.coordinator import (
    MOVE_KINDS,
    answer_owner_question_fn,
    ask_owner_question_fn,
    commit_coordinator_proposal_fn,
    commit_drill_down_fn,
    current_campaign_fn,
    open_comprehension_campaign_fn,
    perform_coordinator_proposal,
    perform_drill_down,
    prepare_coordinator_proposal_fn,
    project_comprehension_campaign_fn,
    propose_next_move_deterministic_fn,
    record_coordinator_move_fn,
    request_drill_down_fn,
    settle_coordinator_move_fn,
    validate_coordinator_move_fn,
)
from packs.subject_synthesis.working import (
    compose_working_understanding_fn,
    contribute_source_lens_fn,
    current_working_understanding_fn,
    pending_reinterpretations_fn,
    project_working_understanding_fn,
    settle_source_lens_fn,
)


@pytest.fixture
def runtime():
    rt = Runtime(Graph())
    rt.load_pack(subject_profile_pack)
    rt.load_pack(subject_synthesis_pack)
    rt.run_until_idle()
    return rt


@pytest.fixture(autouse=True)
def _clean_providers_and_fixture_affordances():
    clear_llm_provider()
    yield
    clear_llm_provider()
    unregister_understanding_affordance(READING_LIST_AFFORDANCE_ID)


def _response(raw: str) -> LLMResponse:
    return LLMResponse(
        raw_text=raw, parsed=None, input_tokens=10, output_tokens=10,
        cost_usd=Decimal("0"), latency_seconds=0.1, model="m",
        finish_reason="end_turn",
    )


class ScriptedProvider:
    def __init__(self, raw: str):
        self.raw = raw
        self.calls = 0

    def complete(self, **kwargs):
        self.calls += 1
        return _response(self.raw)


FIXTURE_RESOLVED = ResolvedLLMProvider(
    provider="anthropic", source="setting",
    api_key_env="ANTHROPIC_API_KEY", model=None,
)


# ---- the fixture-only THIRD affordance (ADR 0047 acceptance) ----------------
#
# A reading-list source: records-family bookmarks, structurally nothing like
# sent mail (no recipients/threads) and nothing like web search (no outward
# queries, no pages) — proving the contract carries neither shape.

READING_LIST_AFFORDANCE_ID = "reading_list_understanding"


def _reading_list_drill_select(reader, bounded):
    rows = [
        {
            "item_ref": f"bookmark:{i}",
            "evidence_refs": [f"evidence:bookmark:{i}"],
            "excerpt": f"saved article {i} about graph runtimes"[
                : bounded["max_excerpt_chars"]
            ],
        }
        for i in range(3)
    ]
    return {"rows": rows[: bounded["max_items"]],
            "excluded": {"beyond_drill_bounds": max(0, 3 - bounded["max_items"])}}


def _reading_list_affordance() -> dict:
    return {
        "affordance_id": READING_LIST_AFFORDANCE_ID,
        "version": "0.1.0",
        "service": "reading_list",
        "family": "records",
        "teaches": ["topics", "interests"],
        "capabilities": [
            {"capability": "bookmarks.list", "action_class": "R1",
             "scopes": ["saved_items"]},
        ],
        "schemas": {
            "input": {"max_items": "int"},
            "output": {"leaf_schema": ["topics", "confidence"]},
            "evidence_ref": "bookmark evidence id",
        },
        "privacy": {
            "excludes": ["private_notes"],
            "outward_disclosure": "provider_only",
        },
        "reductions": {"leaf_schema": ["topics", "confidence"]},
        "drill_down": {
            "allowed": True, "max_items": 2, "max_excerpt_chars": 300,
            "max_context_tokens": 1_000, "select": _reading_list_drill_select,
        },
        "bounds": {"max_items": 50, "max_seconds": 600, "max_tokens": 10_000,
                   "max_cost_milli": 500},
        "moves": ["inspect_source", "reduce_fast", "drill_down"],
        "destinations": ["subject_profile"],
        "coverage_required": True,
    }


# ---- affordance contract ------------------------------------------------------

def test_affordance_declaration_is_validated():
    assert validate_understanding_affordance({"affordance_id": "x"})
    broken = _reading_list_affordance()
    broken["drill_down"] = {"allowed": True, "max_items": 2,
                            "max_excerpt_chars": 300, "max_context_tokens": 1000}
    problems = validate_understanding_affordance(broken)
    assert any("drill_down.select" in p for p in problems)
    with pytest.raises(ValueError, match="invalid understanding affordance"):
        register_understanding_affordance(broken)


def test_outward_query_requires_disclosure_and_gate():
    declaration = _reading_list_affordance()
    declaration["moves"] = ["inspect_source", "outward_query"]
    problems = validate_understanding_affordance(declaration)
    assert any("outward_disclosure is none" not in p for p in problems)
    assert any("outward_gate" in p for p in problems)


def test_third_affordance_registers_and_catalogs(runtime):
    register_understanding_affordance(_reading_list_affordance())
    assert READING_LIST_AFFORDANCE_ID in [
        row["affordance_id"] for row in affordance_catalog_fn(runtime.graph)
    ]
    row = next(
        row for row in affordance_catalog_fn(runtime.graph)
        if row["affordance_id"] == READING_LIST_AFFORDANCE_ID
    )
    # The catalog is the coordinator's discovery surface: bounded facts only.
    assert row["moves"] == ["inspect_source", "reduce_fast", "drill_down"]
    assert row["available"] is True
    assert row["outward_disclosure"] == "provider_only"


def test_gmail_and_web_affordances_are_registered():
    import packs.gmail  # noqa: F401  (pack import registers)
    import packs.web_research  # noqa: F401

    gmail = get_understanding_affordance("gmail_sent_understanding")
    web = get_understanding_affordance("web_research_understanding")
    assert gmail is not None and web is not None
    # Source truth stays source-owned: gmail never queries outward, web
    # never drills into raw items.
    assert "outward_query" not in gmail["moves"]
    assert web["drill_down"]["allowed"] is False
    assert callable(web["outward_gate"])


# ---- source lenses and lineage --------------------------------------------------

def test_lens_contribution_separates_support_from_context(runtime):
    graph = runtime.graph
    result = contribute_source_lens_fn(
        graph, affordance_id="web_research_understanding",
        source_surface_id="web_research:owner",
        contributions=[
            {"kind": "hypothesis", "statement": "works on BabyAGI",
             "support_refs": ["finding:1"], "context_refs": []},
            {"kind": "hypothesis", "statement": "borrowed claim with no evidence",
             "support_refs": [], "context_refs": ["other:source:ref"]},
        ],
        coverage={"pages": 3},
    )
    assert result["appended"] == 1
    assert result["dropped_unsupported"] == 1
    lens = next(obj for obj in graph.objects(type="source_lens"))
    rows = lens.data["contributions"]
    assert rows[0]["support_refs"] == ["finding:1"]


def test_lens_settles_terminally(runtime):
    graph = runtime.graph
    settle_source_lens_fn(
        graph, affordance_id="gmail_sent_understanding",
        source_surface_id="gmail:owner", terminal="declined",
        reason="the owner declined sent-mail study",
    )
    lens = next(obj for obj in graph.objects(type="source_lens"))
    assert lens.data["status"] == "declined"
    assert "declined" in lens.data["terminal_reason"]


# ---- working understanding -------------------------------------------------------

def _seed_fact(graph, attribute: str, value: str):
    """A promoted owner fact in the subject_profile pipeline shape."""
    graph.add_object("subject_fact", {
        "fact_identity": f"fact:{attribute}:{value}",
        "subject_ref": "owner",
        "attribute": attribute,
        "value": value,
        "text": f"{attribute}: {value}",
        "status": "promoted",
        "verdict_id": "verdict:seed",
    })


def test_working_understanding_versions_and_authority(runtime):
    graph = runtime.graph
    _seed_fact(graph, "name", "Ada Example")
    first = compose_working_understanding_fn(graph)
    assert first["created"] and first["version"] == 1
    again = compose_working_understanding_fn(graph)
    assert again["created"] is False, "unchanged content must not mint a version"

    contribute_source_lens_fn(
        graph, affordance_id="web_research_understanding",
        source_surface_id="web_research:owner",
        contributions=[{
            "kind": "hypothesis", "statement": "works on ActiveGraph",
            "support_refs": ["finding:9"],
        }],
    )
    second = compose_working_understanding_fn(graph)
    assert second["created"] and second["version"] == 2
    packet = project_working_understanding_fn(graph)
    by_statement = {row["statement"]: row for row in packet["entries"]}
    assert by_statement["name: Ada Example"]["authority"] == "owner_confirmed"
    assert by_statement["works on ActiveGraph"]["authority"] == "hypothesis"


def test_borrowed_context_never_corroborates(runtime):
    """THE anti-laundering regression (ADR 0047 §3): a public hypothesis fed
    into mail analysis and repeated without mail evidence must not gain
    corroboration or confidence."""
    graph = runtime.graph
    contribute_source_lens_fn(
        graph, affordance_id="web_research_understanding",
        source_surface_id="web_research:owner",
        contributions=[{
            "kind": "hypothesis", "statement": "advises Untapped Capital",
            "support_refs": ["finding:2"], "confidence": 0.6,
        }],
    )
    compose_working_understanding_fn(graph)
    packet = project_working_understanding_fn(graph)
    entry = next(r for r in packet["entries"]
                 if r["statement"] == "advises Untapped Capital")
    assert entry["corroboration"] == 1
    baseline_confidence = entry["confidence"]

    # Mail "repeats" the claim purely from borrowed context: no support refs.
    result = contribute_source_lens_fn(
        graph, affordance_id="gmail_sent_understanding",
        source_surface_id="gmail:owner",
        working_version_read=packet["version"],
        contributions=[{
            "kind": "hypothesis", "statement": "advises Untapped Capital",
            "support_refs": [], "context_refs": ["finding:2"],
            "confidence": 0.9,
        }],
    )
    assert result["dropped_unsupported"] == 1
    compose_working_understanding_fn(graph)
    packet = project_working_understanding_fn(graph)
    entry = next(r for r in packet["entries"]
                 if r["statement"] == "advises Untapped Capital")
    assert entry["corroboration"] == 1, "context-only repetition corroborated"
    assert entry["confidence"] == baseline_confidence

    # Genuine mail evidence DOES corroborate — with the borrowed context
    # still recorded as context, not support.
    contribute_source_lens_fn(
        graph, affordance_id="gmail_sent_understanding",
        source_surface_id="gmail:owner",
        contributions=[{
            "kind": "hypothesis", "statement": "advises Untapped Capital",
            "support_refs": ["message:77"], "context_refs": ["finding:2"],
            "confidence": 0.7, "entry_key": "mail-own-evidence",
        }],
    )
    compose_working_understanding_fn(graph)
    packet = project_working_understanding_fn(graph)
    entry = next(r for r in packet["entries"]
                 if r["statement"] == "advises Untapped Capital")
    assert entry["corroboration"] == 2
    assert entry["confidence"] > baseline_confidence
    assert {row["source"] for row in entry["support"]} == {
        "web_research_understanding", "gmail_sent_understanding",
    }


def test_material_change_schedules_targeted_reinterpretation_only(runtime):
    graph = runtime.graph
    _seed_fact(graph, "name", "Ada Example")
    compose_working_understanding_fn(graph)
    assert pending_reinterpretations_fn(graph) == [], "v1 reinterprets nothing"

    contribute_source_lens_fn(
        graph, affordance_id="web_research_understanding",
        source_surface_id="web_research:owner",
        working_version_read=1,
        contributions=[{
            "kind": "entity", "statement": "Untapped Capital is an organization",
            "support_refs": ["finding:4"],
        }],
    )
    compose_working_understanding_fn(graph)
    pending = pending_reinterpretations_fn(graph)
    kinds = {row["target_kind"] for row in pending}
    assert "synthesis" in kinds
    # Targeted, never global: no source acquisition is re-run, and the only
    # lens alignment scheduled is for lenses pinned to an older version.
    assert all(row["target_kind"] in ("synthesis", "lens_alignment")
               for row in pending)
    # Composing again without material change schedules nothing new.
    before = len(pending_reinterpretations_fn(graph))
    compose_working_understanding_fn(graph)
    assert len(pending_reinterpretations_fn(graph)) == before


# ---- campaign + validator ---------------------------------------------------------

def _open_campaign(graph, affordances=("web_research_understanding",)):
    result = open_comprehension_campaign_fn(
        graph, selected_affordances=list(affordances),
    )
    return current_campaign_fn(graph)


def test_campaign_opens_idempotently_and_extends_cohort(runtime):
    graph = runtime.graph
    first = open_comprehension_campaign_fn(
        graph, selected_affordances=["web_research_understanding"],
    )
    assert first["created"]
    second = open_comprehension_campaign_fn(
        graph, selected_affordances=["gmail_sent_understanding"],
    )
    assert second["created"] is False
    campaign = current_campaign_fn(graph)
    assert campaign.data["selected_affordances"] == [
        "web_research_understanding", "gmail_sent_understanding",
    ]
    assert campaign.data["budgets"]["max_moves"] > 0
    assert "model_roles" in campaign.data["pins"]


def test_validator_rejects_before_it_pauses(runtime):
    graph = runtime.graph
    campaign = _open_campaign(graph)

    unknown = validate_coordinator_move_fn(graph, campaign, {"kind": "invent"})
    assert unknown["verdict"] == "reject"

    unselected = validate_coordinator_move_fn(graph, campaign, {
        "kind": "reduce_fast", "affordance_id": "gmail_sent_understanding",
        "params": {"source_surface_id": "gmail:owner"},
    })
    assert unselected["verdict"] == "reject"
    assert any("affordance_not_selected" in r for r in unselected["reasons"])

    register_understanding_affordance(_reading_list_affordance())
    campaign = _open_campaign(graph, (
        "web_research_understanding", READING_LIST_AFFORDANCE_ID,
    ))
    bad_capability = validate_coordinator_move_fn(graph, campaign, {
        "kind": "inspect_source", "affordance_id": READING_LIST_AFFORDANCE_ID,
        "capability": "bookmarks.delete",
        "params": {"source_surface_id": "reading_list:owner"},
    })
    assert bad_capability["verdict"] == "reject"
    assert any("capability_not_declared" in r for r in bad_capability["reasons"])

    bad_scope = validate_coordinator_move_fn(graph, campaign, {
        "kind": "inspect_source", "affordance_id": READING_LIST_AFFORDANCE_ID,
        "capability": "bookmarks.list",
        "params": {"scope": "everything", "source_surface_id": "reading_list:owner"},
    })
    assert bad_scope["verdict"] == "reject"
    assert any("scope_not_declared" in r for r in bad_scope["reasons"])


def test_validator_requires_consented_plan_for_source_reads(runtime):
    graph = runtime.graph
    register_understanding_affordance(_reading_list_affordance())
    campaign = _open_campaign(graph, (READING_LIST_AFFORDANCE_ID,))
    move = {
        "kind": "reduce_fast", "affordance_id": READING_LIST_AFFORDANCE_ID,
        "capability": "bookmarks.list",
        "params": {"source_surface_id": "reading_list:owner"},
    }
    set_llm_provider(ScriptedProvider("{}"), FIXTURE_RESOLVED)
    verdict = validate_coordinator_move_fn(graph, campaign, move)
    assert verdict["verdict"] == "reject"
    assert any("no_approved_plan" in r for r in verdict["reasons"])


def test_validator_zero_key_rejects_model_moves(runtime):
    graph = runtime.graph
    register_understanding_affordance(_reading_list_affordance())
    campaign = _open_campaign(graph, (READING_LIST_AFFORDANCE_ID,))
    graph.add_object("connector_ingestion_plan", _plan_data(
        "reading_list", "reading_list:owner", status="approved",
        purpose="comprehension",
    ))
    move = {
        "kind": "reduce_fast", "affordance_id": READING_LIST_AFFORDANCE_ID,
        "capability": "bookmarks.list",
        "params": {"source_surface_id": "reading_list:owner"},
    }
    verdict = validate_coordinator_move_fn(graph, campaign, move)
    assert verdict["verdict"] == "reject"
    assert "provider_unavailable" in verdict["reasons"]


def _plan_data(service: str, surface: str, *, status: str, purpose: str,
               version: int = 1) -> dict:
    return {
        "plan_identity": f"plan:{service}:{purpose}",
        "source_surface_id": surface,
        "service": service,
        "account_ref": "owner",
        "family": "records",
        "purpose": purpose,
        "status": status,
        "version": version,
        "window": {"kind": "recent_items"},
        "derivation": {"basis": "service_default", "summary": "fixture",
                       "measurements": {}, "provenance": []},
        "surfaces": [],
        "caps": {"max_items": 10},
        "interpretation_stages": [],
        "proposed_by": "fixture@0.1.0",
        "metadata": {},
    }


def test_validator_outward_query_needs_owner_confirmed_derivation(runtime):
    graph = runtime.graph
    import packs.web_research  # ensure affordance registered  # noqa: F401

    campaign = _open_campaign(graph)
    _seed_fact(graph, "name", "Ada Example")
    contribute_source_lens_fn(
        graph, affordance_id="web_research_understanding",
        source_surface_id="web_research:owner",
        contributions=[{
            "kind": "hypothesis", "statement": "maybe works at ExampleCorp",
            "support_refs": ["finding:1"],
        }],
    )
    compose_working_understanding_fn(graph)
    packet = project_working_understanding_fn(graph)
    fact_entry = next(r for r in packet["entries"]
                      if r["authority"] == "owner_confirmed")
    hypothesis_entry = next(r for r in packet["entries"]
                            if r["authority"] == "hypothesis")

    move = {
        "kind": "outward_query", "affordance_id": "web_research_understanding",
        "capability": "model_search",
        "params": {"query": '"Ada Example" ExampleCorp',
                   "derived_from_entries": [hypothesis_entry["entry_id"]]},
    }
    verdict = validate_coordinator_move_fn(graph, campaign, move)
    assert verdict["verdict"] == "reject"
    assert any("derivation_not_owner_confirmed" in r for r in verdict["reasons"])

    move["params"]["derived_from_entries"] = [fact_entry["entry_id"]]
    # No approved research plan exists → the source-owned gate rejects.
    verdict = validate_coordinator_move_fn(graph, campaign, move)
    assert verdict["verdict"] == "reject"
    assert any("outward_gate" in r for r in verdict["reasons"])


def test_validator_budget_exhaustion_pauses_for_owner(runtime):
    graph = runtime.graph
    open_comprehension_campaign_fn(
        graph, selected_affordances=["web_research_understanding"],
        budgets={"max_moves": 0},
    )
    campaign = current_campaign_fn(graph)
    verdict = validate_coordinator_move_fn(graph, campaign, {
        "kind": "align_entities", "params": {},
    })
    assert verdict["verdict"] == "pause_owner"
    assert "move_budget_exhausted" in verdict["reasons"]


def test_drill_down_validation_enforces_declared_bounds(runtime):
    graph = runtime.graph
    register_understanding_affordance(_reading_list_affordance())
    campaign = _open_campaign(graph, (READING_LIST_AFFORDANCE_ID,))
    graph.add_object("connector_ingestion_plan", _plan_data(
        "reading_list", "reading_list:owner", status="approved",
        purpose="comprehension",
    ))
    set_llm_provider(ScriptedProvider("{}"), FIXTURE_RESOLVED)
    over = validate_coordinator_move_fn(graph, campaign, {
        "kind": "drill_down", "affordance_id": READING_LIST_AFFORDANCE_ID,
        "capability": "bookmarks.list",
        "params": {"source_surface_id": "reading_list:owner",
                   "question": "what topics recur?",
                   "max_items": 10, "max_excerpt_chars": 300},
    })
    assert over["verdict"] == "reject"
    assert "drill_down_items_out_of_bounds" in over["reasons"]

    ok = validate_coordinator_move_fn(graph, campaign, {
        "kind": "drill_down", "affordance_id": READING_LIST_AFFORDANCE_ID,
        "capability": "bookmarks.list",
        "params": {"source_surface_id": "reading_list:owner",
                   "question": "what topics recur?",
                   "max_items": 2, "max_excerpt_chars": 300},
    })
    assert ok["verdict"] == "execute"

    web_campaign = _open_campaign(graph)
    never = validate_coordinator_move_fn(graph, web_campaign, {
        "kind": "drill_down", "affordance_id": "web_research_understanding",
        "params": {"source_surface_id": "web_research:owner",
                   "question": "?", "max_items": 1, "max_excerpt_chars": 100},
    })
    assert never["verdict"] == "reject"


# ---- recorded moves + owner questions ------------------------------------------

def test_recorded_move_carries_validation_and_pauses_campaign(runtime):
    graph = runtime.graph
    campaign = _open_campaign(graph)
    result = record_coordinator_move_fn(graph, campaign.id, {
        "kind": "ask_owner",
        "rationale": "two plausible public identities need distinguishing",
        "params": {"question": "which is you?"},
        "requires_owner": True,
    }, proposer={"kind": "deterministic"})
    assert result["verdict"] == "pause_owner"
    campaign = current_campaign_fn(graph)
    assert campaign.data["status"] == "paused_owner"
    move = next(obj for obj in graph.objects(type="coordinator_move"))
    assert move.data["status"] == "paused"
    assert move.data["validation"]["verdict"] == "pause_owner"
    assert move.data["proposer"]["kind"] == "deterministic"

    # The pause is answerable in the same commit: recording a pausing
    # ask_owner mints its owner question (a paused campaign with nothing
    # to answer is a deadlock, not a state).
    question = next(
        (obj for obj in graph.objects(type="owner_question")
         if obj.data.get("status") == "open"),
        None,
    )
    assert question is not None, "the pausing ask_owner minted its question"
    assert question.data["prompt"] == "which is you?"
    assert question.data["move_ref"] == move.id

    # While paused, nothing else validates as executable.
    verdict = validate_coordinator_move_fn(graph, campaign, {
        "kind": "align_entities", "params": {},
    })
    assert verdict["verdict"] == "reject"
    assert "campaign_paused_for_owner" in verdict["reasons"]


def test_ask_owner_without_question_is_rejected_not_stranded(runtime):
    """A model ask_owner with no question text must NOT pause the campaign:
    there would be nothing for the owner to answer (the deadlock a live run
    hit on 2026-07-14). It rejects with a reason the proposer sees on its
    next window move."""
    graph = runtime.graph
    campaign = _open_campaign(graph)
    result = record_coordinator_move_fn(graph, campaign.id, {
        "kind": "ask_owner",
        "rationale": "must establish review scope and priorities",
        "params": {},
        "requires_owner": True,
    }, proposer={"kind": "model"})
    assert result["verdict"] == "reject"
    assert "ask_owner_needs_question" in result["reasons"]
    assert current_campaign_fn(graph).data["status"] == "open"
    assert not list(graph.objects(type="owner_question"))


def test_owner_question_answer_resumes_within_confirmed_scope(runtime):
    graph = runtime.graph
    campaign = _open_campaign(graph)
    asked = ask_owner_question_fn(
        graph, campaign.id, kind="identity_ambiguity",
        prompt="I found two plausible people. Which is you?",
        options=[
            {"id": "a", "label": "Ada Example — ExampleCorp founder",
             "evidence_refs": ["finding:1"]},
            {"id": "b", "label": "Ada Example — photographer",
             "evidence_refs": ["finding:2"]},
        ],
    )
    assert asked["created"]
    assert current_campaign_fn(graph).data["status"] == "paused_owner"
    # Idempotent while open.
    again = ask_owner_question_fn(
        graph, campaign.id, kind="identity_ambiguity",
        prompt="I found two plausible people. Which is you?",
    )
    assert again["created"] is False

    answer = answer_owner_question_fn(graph, asked["question_id"], option_id="a")
    assert answer["ok"]
    campaign = current_campaign_fn(graph)
    assert campaign.data["status"] == "open", "answer resumes without ceremony"
    question = graph.get_object(asked["question_id"])
    assert question.data["status"] == "answered"
    assert question.data["answer"]["option_id"] == "a"


# ---- deterministic proposer (zero-key floor) -------------------------------------

def test_deterministic_proposer_waits_synthesizes_then_stops(runtime):
    graph = runtime.graph
    campaign = _open_campaign(graph, ("web_research_understanding",))
    # Source in flight (lens pending): no busy-wait moves.
    contribute_source_lens_fn(
        graph, affordance_id="web_research_understanding",
        source_surface_id="web_research:owner", contributions=[],
    )
    assert propose_next_move_deterministic_fn(graph, campaign) is None

    settle_source_lens_fn(
        graph, affordance_id="web_research_understanding",
        source_surface_id="web_research:owner", terminal="contributed",
    )
    move = propose_next_move_deterministic_fn(graph, campaign)
    assert move is not None and move["kind"] == "synthesize"
    verdict = validate_coordinator_move_fn(graph, campaign, move)
    assert verdict["verdict"] == "execute"

    # Draft resolved → stop.
    graph.add_object("setup_draft", {
        "draft_identity": "draft:v1", "version": 1, "subject_ref": "owner",
        "status": "submitted", "source": "deterministic", "run_id": None,
        "supersedes": None, "included_refs": [], "coverage": {},
        "counts": {}, "metadata": {},
    })
    move = propose_next_move_deterministic_fn(graph, campaign)
    assert move is not None and move["kind"] == "stop"
    assert move["params"]["stop_reason"] == "review_ready"


# ---- the model proposer (fake-provider conformance) ------------------------------

def test_model_proposal_rides_prepare_perform_commit(runtime):
    graph = runtime.graph
    campaign = _open_campaign(graph)
    _seed_fact(graph, "name", "Ada Example")
    compose_working_understanding_fn(graph)

    staged = prepare_coordinator_proposal_fn(graph, campaign.id)
    assert staged["ok"]
    payload = staged["payload"]
    assert payload["move_kinds"] == list(MOVE_KINDS)
    assert "synthesize" in payload["available_move_kinds"]
    assert payload["working"]["entries"], "the packet carries the working entries"
    assert "remaining" in payload

    proposal = json.dumps({
        "kind": "align_entities",
        "rationale": "align the confirmed name with research findings",
        "expected_gain": "resolved aliases",
        "params": {},
        "cost": {"tokens": 200},
    })
    set_llm_provider(ScriptedProvider(proposal), FIXTURE_RESOLVED)
    outcome = perform_coordinator_proposal(payload)
    assert outcome["ok"] and outcome["model_role"] == "reasoning"

    committed = commit_coordinator_proposal_fn(graph, campaign.id, payload, outcome)
    assert committed["ok"] and committed["verdict"] == "execute"
    move = next(obj for obj in graph.objects(type="coordinator_move"))
    assert move.data["proposer"]["kind"] == "model"
    assert move.data["proposer"]["model_role"] == "reasoning"

    settle_coordinator_move_fn(
        graph, move.id, status="committed",
        result={"summary": "aliases aligned"}, cost={"tokens": 200},
    )
    campaign = current_campaign_fn(graph)
    assert campaign.data["spent"]["moves"] == 1
    assert campaign.data["spent"]["tokens"] == 200


def test_model_proposing_forbidden_move_is_recorded_rejected(runtime):
    graph = runtime.graph
    campaign = _open_campaign(graph)
    staged = prepare_coordinator_proposal_fn(graph, campaign.id)
    proposal = json.dumps({
        "kind": "drill_down",
        "affordance_id": "web_research_understanding",
        "rationale": "read the raw pages",
        "params": {"source_surface_id": "web_research:owner",
                   "question": "everything", "max_items": 50,
                   "max_excerpt_chars": 100000},
    })
    set_llm_provider(ScriptedProvider(proposal), FIXTURE_RESOLVED)
    outcome = perform_coordinator_proposal(staged["payload"])
    committed = commit_coordinator_proposal_fn(
        graph, campaign.id, staged["payload"], outcome
    )
    assert committed["verdict"] == "reject"
    move = next(obj for obj in graph.objects(type="coordinator_move"))
    assert move.data["status"] == "rejected"
    assert move.data["validation"]["reasons"], "the audit record names why"


def test_proposal_packet_teaches_rejections_and_availability(runtime):
    """The packet must make rejections learnable (verdict_reasons on prior
    moves) and unavailability explicit (source moves without a selected
    affordance) — a proposer that can't see WHY a move bounced re-proposes
    it verbatim, burning the whole window (live run, 2026-07-14)."""
    from packs.subject_synthesis.coordinator import SOURCE_MOVE_KINDS

    graph = runtime.graph
    campaign = _open_campaign(graph, ())  # nothing selected
    rejected = record_coordinator_move_fn(graph, campaign.id, {
        "kind": "inspect_source",
        "rationale": "read owner-provided facts to establish baseline",
        "params": {},
    }, proposer={"kind": "model"})
    assert rejected["verdict"] == "reject"

    staged = prepare_coordinator_proposal_fn(graph, campaign.id)
    assert staged["ok"]
    payload = staged["payload"]
    last = payload["prior_moves"][-1]
    assert last["status"] == "rejected"
    assert "source_move_needs_affordance" in last["verdict_reasons"]
    for kind in SOURCE_MOVE_KINDS:
        assert payload["unavailable_move_kinds"][kind] == "no_selected_affordance"
        assert kind not in payload["available_move_kinds"]
    for kind in ("ask_owner", "synthesize", "stop", "align_entities"):
        assert kind in payload["available_move_kinds"]
    # The prompt steers by availability, not the full grammar.
    from packs.subject_synthesis.coordinator import _proposal_prompt

    system, user = _proposal_prompt(payload)
    assert "available_move_kinds" in user
    assert "never re-propose a rejected move unchanged" in system.lower()


def test_unparseable_proposal_is_a_recorded_non_event(runtime):
    graph = runtime.graph
    campaign = _open_campaign(graph)
    staged = prepare_coordinator_proposal_fn(graph, campaign.id)
    set_llm_provider(ScriptedProvider("I think we should look around."),
                     FIXTURE_RESOLVED)
    outcome = perform_coordinator_proposal(staged["payload"])
    assert outcome["ok"] is False
    committed = commit_coordinator_proposal_fn(
        graph, campaign.id, staged["payload"], outcome
    )
    assert committed["ok"] is False
    campaign = current_campaign_fn(graph)
    assert campaign.data["metadata"]["proposal_failures"]
    assert list(graph.objects(type="coordinator_move")) == []


# ---- bounded evidence drill-down ---------------------------------------------------

def test_drill_down_records_selection_and_drops_uncited_findings(runtime):
    graph = runtime.graph
    register_understanding_affordance(_reading_list_affordance())
    campaign = _open_campaign(graph, (READING_LIST_AFFORDANCE_ID,))

    staged = request_drill_down_fn(
        graph, campaign_ref=campaign.id, move_ref="move:1",
        affordance_id=READING_LIST_AFFORDANCE_ID,
        question="what topics does the owner save?",
        params={"max_items": 2, "max_excerpt_chars": 200},
    )
    assert staged["ok"]
    payload = staged["payload"]
    assert len(payload["rows"]) == 2, "bounded by the requested max_items"
    drill = graph.get_object(staged["drill_id"])
    assert drill.data["included_refs"] == ["bookmark:0", "bookmark:1"]
    assert drill.data["excluded"]["beyond_drill_bounds"] == 1

    findings = json.dumps({
        "findings": [
            {"statement": "saves graph-runtime articles",
             "item_refs": ["bookmark:0"], "confidence": 0.8},
            {"statement": "cites an item that was never included",
             "item_refs": ["bookmark:99"], "confidence": 0.9},
        ],
    })
    set_llm_provider(ScriptedProvider(findings), FIXTURE_RESOLVED)
    outcome = perform_drill_down(payload)
    assert outcome["ok"] and outcome["model_role"] == "reasoning"
    committed = commit_drill_down_fn(graph, staged["drill_id"], payload, outcome)
    assert committed["findings"] == 1
    assert committed["dropped_uncited"] == 1
    drill = graph.get_object(staged["drill_id"])
    assert drill.data["status"] == "committed"
    assert drill.data["findings"][0]["item_refs"] == ["bookmark:0"]
    assert drill.data["findings"][0]["evidence_refs"] == ["evidence:bookmark:0"]

    # Commit is idempotent (crash-after-commit replay).
    again = commit_drill_down_fn(graph, staged["drill_id"], payload, outcome)
    assert again.get("already_committed")


def test_drill_down_refused_without_selector_permission(runtime):
    graph = runtime.graph
    campaign = _open_campaign(graph)
    refused = request_drill_down_fn(
        graph, campaign_ref=campaign.id, move_ref="",
        affordance_id="web_research_understanding",
        question="read everything",
    )
    assert refused["ok"] is False
    assert refused["reason"] == "drill_down_not_allowed"
    assert list(graph.objects(type="evidence_drill_down")) == []


# ---- projection ---------------------------------------------------------------------

def test_campaign_projection_shape(runtime):
    graph = runtime.graph
    campaign = _open_campaign(graph)
    record_coordinator_move_fn(graph, campaign.id, {
        "kind": "align_entities", "rationale": "align aliases", "params": {},
    })
    ask_owner_question_fn(
        graph, campaign.id, kind="differentiating",
        prompt="which handle is primary?",
    )
    projected = project_comprehension_campaign_fn(graph)
    assert projected["exists"]
    assert projected["status"] == "paused_owner"
    assert len(projected["moves"]) == 1
    assert projected["moves"][0]["validation"]["verdict"] == "execute"
    assert len(projected["open_questions"]) == 1
    assert projected["pins"]["model_roles"]["fast"] is not None


def test_frozen_cohort_lifts_the_settled_wait(runtime):
    """“Review what I have now” (ADR 0048 §3): a frozen cohort synthesizes
    despite a still-working selected source; the late source's results
    arrive as deltas, never as a moved snapshot."""
    from packs.subject_synthesis.coordinator import (
        freeze_review_cohort_fn, review_cohort_state_fn,
    )

    graph = runtime.graph
    campaign = _open_campaign(graph, ("web_research_understanding",))
    contribute_source_lens_fn(
        graph, affordance_id="web_research_understanding",
        source_surface_id="web_research:owner", contributions=[],
    )
    assert propose_next_move_deterministic_fn(graph, campaign) is None

    frozen = freeze_review_cohort_fn(graph, campaign.id, event_horizon="evt_42")
    assert frozen["ok"] and frozen["frozen"]
    assert freeze_review_cohort_fn(graph, campaign.id)["already_frozen"]
    state = review_cohort_state_fn(graph)
    assert state["frozen"] and state["frozen_horizon"] == "evt_42"
    assert state["pending"] == ["web_research_understanding"]

    campaign = current_campaign_fn(graph)
    move = propose_next_move_deterministic_fn(graph, campaign)
    assert move is not None and move["kind"] == "synthesize"
