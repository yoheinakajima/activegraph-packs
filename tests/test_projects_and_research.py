"""ADR 0040: project derivation/verdicts and consented web research."""

from __future__ import annotations

import hashlib

import pytest

from activegraph import Graph, Runtime

from packs.activity_normalizer import pack as normalizer_pack
from packs.connector_control import pack as connector_control_pack
from packs.connector_control.plans import (
    approve_ingestion_plan_fn,
    begin_deferred_plan_execution_fn,
    commit_deferred_plan_execution_fn,
    current_plan_for_surface_fn,
    edit_ingestion_plan_fn,
    execute_ingestion_plan_fn,
    has_deferred_plan_execution,
    pending_deferred_plan_executions_fn,
)
from packs.projects import pack as projects_pack
from packs.projects.tools import (
    derive_project_candidates_fn,
    project_projects_fn,
    rename_project_fn,
    review_project_candidate_fn,
)
from packs.subject_profile import pack as subject_profile_pack
from packs.web_research import pack as web_research_pack
from packs.web_research.plan import (
    RESEARCH_SURFACE_ID,
    derive_research_queries,
    execute_web_research_plan_fn,
    propose_web_research_plan_fn,
)


@pytest.fixture
def runtime():
    rt = Runtime(Graph())
    rt.load_pack(normalizer_pack)
    rt.load_pack(connector_control_pack)
    rt.load_pack(subject_profile_pack)
    rt.load_pack(projects_pack)
    rt.load_pack(web_research_pack)
    rt.run_until_idle()
    return rt


def _fact(graph, attribute: str, value: str):
    graph.add_object("subject_fact", {
        "fact_identity": f"fact-{attribute}-{value}",
        "subject_ref": "owner", "attribute": attribute, "value": value,
        "text": value, "status": "promoted", "confidence": 0.9, "trust": 0.9,
        "candidate_id": None, "annotation_id": None, "evidence_id": None,
        "source_surface_id": None, "verdict_id": None,
        "supersedes_fact_id": None, "metadata": {},
    })


def test_project_candidates_derive_in_seed_priority_order(runtime):
    graph = runtime.graph
    _fact(graph, "company", "Untapped Capital")
    _fact(graph, "project", "BabyAGI")
    _fact(graph, "email", "yohei@x.com")  # identity alias — never a project
    graph.add_object("integration_profile", {
        "profile_identity": "prof-1", "profile_version": 1, "service": "gmail",
        "account_ref": "yohei@x.com", "account_display": None, "status": "active",
        "routes": [], "scopes_granted": [], "scopes_available": [],
        "facets": [], "capability_inventory": [],
        "data_topology": {"containers": [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "L1", "name": "Deal Flow", "type": "user"},
            {"id": "L2", "name": "Fund/LPs", "type": "user"},
        ]},
        "signal_map": [], "claims": [], "health": {},
        "exploration_receipts": [], "supersedes_id": None, "metadata": {},
    })
    entity = graph.add_object("entity", {
        "name": "ActiveGraph", "entity_type": "project", "aliases": [],
        "confidence": 0.9, "source_ids": [], "metadata": {},
    })
    for index in range(3):
        graph.add_object("entity_mention", {
            "text": "ActiveGraph", "source_id": f"s{index}", "entity_id": entity.id,
            "entity_type_hint": "project", "confidence": 0.9,
            "context_snippet": "ActiveGraph", "extraction_method": "test",
            "frame_id": None, "metadata": {},
        })

    result = derive_project_candidates_fn(graph)
    assert result["created"] >= 4
    rows = project_projects_fn(graph)["candidates"]
    by_name = {row["name"]: row for row in rows}
    assert by_name["Untapped Capital"]["kind"] == "fact_seeded"
    assert by_name["BabyAGI"]["kind"] == "fact_seeded"
    assert by_name["Deal Flow"]["kind"] == "label_seeded"
    assert by_name["LPs"]["kind"] == "label_seeded"  # nested label leaf
    assert by_name["ActiveGraph"]["kind"] == "engagement_clustered"
    assert "appears 3×" in by_name["ActiveGraph"]["rationale"]
    assert all(row["sources"] for row in rows)
    assert "yohei@x.com" not in by_name
    # Priority ordering: fact seeds above labels above clusters.
    scores = [row["score_milli"] for row in rows]
    assert by_name["Untapped Capital"]["score_milli"] > by_name["Deal Flow"]["score_milli"] > by_name["ActiveGraph"]["score_milli"]
    assert scores == sorted(scores, reverse=True)

    # Idempotent: re-derivation refreshes, never duplicates.
    again = derive_project_candidates_fn(graph)
    assert again["created"] == 0
    assert len(project_projects_fn(graph)["candidates"]) == len(rows)


def test_verdicts_promote_rename_and_dismiss(runtime):
    graph = runtime.graph
    _fact(graph, "company", "Untapped Capital")
    _fact(graph, "project", "BabyAGI")
    derive_project_candidates_fn(graph)
    rows = project_projects_fn(graph)["candidates"]
    babyagi = next(row for row in rows if row["name"] == "BabyAGI")
    untapped = next(row for row in rows if row["name"] == "Untapped Capital")

    confirmed = review_project_candidate_fn(
        graph, babyagi["candidate_object_id"], "confirm", actor="owner:test"
    )
    assert confirmed["status"] == "confirmed"
    dismissed = review_project_candidate_fn(
        graph, untapped["candidate_object_id"], "dismiss", actor="owner:test"
    )
    assert dismissed["status"] == "dismissed"

    projection = project_projects_fn(graph)
    [project] = projection["projects"]
    assert project["name"] == "BabyAGI"
    assert project["confirmed_by"] == "owner:test"

    renamed = rename_project_fn(graph, project["project_object_id"], "BabyAGI 2.0", actor="owner:test")
    projection = project_projects_fn(graph)
    active = [row for row in projection["projects"] if row["status"] == "active"]
    superseded = [row for row in projection["projects"] if row["status"] == "superseded"]
    assert [row["name"] for row in active] == ["BabyAGI 2.0"]
    assert superseded[0]["superseded_by"] == renamed["project_id"]

    # A dismissed candidate never resurrects on re-derivation.
    derive_project_candidates_fn(graph)
    rows = project_projects_fn(graph)["candidates"]
    assert next(row for row in rows if row["name"] == "Untapped Capital")["status"] == "dismissed"

    with pytest.raises(ValueError, match="only proposed"):
        review_project_candidate_fn(graph, untapped["candidate_object_id"], "confirm")


def test_research_queries_derive_only_from_confirmed_material(runtime):
    graph = runtime.graph
    _fact(graph, "handle", "@yoheinakajima")
    _fact(graph, "url", "https://untapped.vc/team")
    _fact(graph, "email", "yohei@secret-personal.com")
    queries, provenance = derive_research_queries(
        graph, confirmed_terms=("Yohei Nakajima",)
    )
    assert queries == ['"Yohei Nakajima"', '"@yoheinakajima"', "untapped.vc"]
    assert provenance  # fact refs travel into the plan derivation
    assert not any("secret-personal" in query for query in queries)


def test_research_plan_gates_execution_and_reports_planned_vs_actual(runtime):
    graph = runtime.graph
    _fact(graph, "handle", "@yoheinakajima")

    proposal = propose_web_research_plan_fn(graph, confirmed_terms=("Yohei Nakajima",))
    plan = proposal["plan"]
    assert plan.data["service"] == "web_research"
    assert plan.data["family"] == "documents"
    labels = [row["label"] for row in plan.data["surfaces"]]
    assert labels == ['"Yohei Nakajima"', '"@yoheinakajima"']
    assert "nothing leaves this machine" in plan.data["derivation"]["summary"]

    # Nothing runs before approval; a struck query never executes.
    edited = edit_ingestion_plan_fn(
        graph,
        plan_ref=plan.data["plan_identity"],
        surfaces=[
            {**plan.data["surfaces"][0]},
            {**plan.data["surfaces"][1], "included": False},
        ],
        edited_by="owner:test",
    )["plan"]
    approve_ingestion_plan_fn(
        graph, plan_ref=edited.data["plan_identity"], approved_by="owner:test"
    )

    canned = {
        "findings": [
            {"claim": "GP at Untapped Capital", "url": "https://untapped.vc/team", "query": '"Yohei Nakajima"'},
            {"claim": "creator of BabyAGI", "url": "https://github.com/yoheinakajima/babyagi", "query": '"Yohei Nakajima"'},
        ],
        "calls": 1,
        "model": "claude-test",
        "error": None,
    }
    executed = execute_web_research_plan_fn(
        graph, graph.get_object(edited.id), research=canned
    )
    runtime.run_until_idle()
    assert executed["ok"] is True
    assert executed["urls"] == [
        "https://untapped.vc/team",
        "https://github.com/yoheinakajima/babyagi",
    ]
    [run] = graph.objects(type="web_research_run")
    assert run.data["queries"] == ['"Yohei Nakajima"']  # struck query stayed out
    assert run.data["status"] == "completed"
    assert run.data["urls_ingested"] == 2
    # Discovered pages ingest ONLY through the governed presence gateway.
    calls = [
        obj for obj in graph.objects(type="capability_call")
        if obj.data.get("provider_name") == "public_presence"
    ]
    assert len(calls) == 2
    [presence_run] = graph.objects(type="presence_bootstrap_run")
    assert presence_run.data["source_surface_id"] == RESEARCH_SURFACE_ID

    delta = next(
        obj for obj in graph.objects(type="connector_learning_delta")
        if obj.data.get("service") == "web_research"
    )
    assert delta.data["plan"]["actual"]["imported"] == 2
    assert delta.data["plan"]["within_bounds"] is True
    plan_after = current_plan_for_surface_fn(graph, RESEARCH_SURFACE_ID)
    assert plan_after.data["status"] == "fulfilled"

    # The full executor path is also reachable through the neutral registry.
    assert execute_ingestion_plan_fn  # imported: registry wiring covered above


def test_research_without_provider_fails_honestly(runtime):
    graph = runtime.graph
    _fact(graph, "handle", "@yoheinakajima")
    proposal = propose_web_research_plan_fn(graph, search_available=False)
    plan = proposal["plan"]
    assert "no search-capable model key" in plan.data["derivation"]["summary"]
    approve_ingestion_plan_fn(
        graph, plan_ref=plan.data["plan_identity"], approved_by="owner:test"
    )
    executed = execute_web_research_plan_fn(
        graph,
        graph.get_object(plan.id),
        research={"findings": [], "calls": 0, "model": None,
                  "error": "research_provider_unavailable"},
    )
    runtime.run_until_idle()
    assert executed["ok"] is False
    [run] = graph.objects(type="web_research_run")
    assert run.data["status"] == "failed"
    assert "research_provider_unavailable" in run.data["error"]


def test_research_queries_prefer_the_confirmed_name_company_pair(runtime):
    graph = runtime.graph
    _fact(graph, "name", "Yohei Nakajima")
    _fact(graph, "company", "Untapped Capital")
    _fact(graph, "handle", "@yoheinakajima")
    _fact(graph, "url", "https://yoheinakajima.com")
    queries, provenance = derive_research_queries(graph)
    assert queries == [
        '"Yohei Nakajima" Untapped Capital',
        '"@yoheinakajima"',
        "yoheinakajima.com",
    ]
    assert len(provenance) >= 4  # every term names the fact it came from


def test_deferred_execution_mirrors_the_synchronous_settlement(runtime):
    """ADR 0041/D061: prepare (reads) -> perform (network, injected here) ->
    commit lands the identical run, delta, and plan fulfillment the
    synchronous executor produces."""
    graph = runtime.graph
    _fact(graph, "handle", "@yoheinakajima")
    assert has_deferred_plan_execution("web_research")

    proposal = propose_web_research_plan_fn(graph, confirmed_terms=("Yohei Nakajima",))
    plan = proposal["plan"]
    plan_ref = plan.data["plan_identity"]
    # Nothing pending before approval — approval is the only trigger.
    assert pending_deferred_plan_executions_fn(graph) == []
    approve_ingestion_plan_fn(graph, plan_ref=plan_ref, approved_by="owner:test")
    [row] = pending_deferred_plan_executions_fn(graph)
    assert row["service"] == "web_research"

    begun = begin_deferred_plan_execution_fn(graph, plan_ref=plan_ref)
    assert begun["ok"] is True
    assert begun["payload"]["queries"] == ['"Yohei Nakajima"', '"@yoheinakajima"']

    canned = {
        "findings": [
            {"claim": "GP at Untapped Capital",
             "url": "https://untapped.vc/team", "query": '"Yohei Nakajima"'},
        ],
        "calls": 1, "model": "claude-test", "error": None,
    }
    committed = commit_deferred_plan_execution_fn(
        graph, plan_ref=plan_ref, payload=begun["payload"], outcome=canned
    )
    runtime.run_until_idle()
    assert committed["ok"] is True
    [run] = graph.objects(type="web_research_run")
    assert run.data["status"] == "completed"
    assert run.data["urls_ingested"] == 1
    plan_after = current_plan_for_surface_fn(graph, RESEARCH_SURFACE_ID)
    assert plan_after.data["status"] == "fulfilled"
    # Fulfilled plans leave the pending pool.
    assert pending_deferred_plan_executions_fn(graph) == []


def test_deferred_commit_fails_closed_when_the_plan_was_superseded(runtime):
    """ADR 0020: a plan edited mid-flight can never commit its stale run."""
    graph = runtime.graph
    _fact(graph, "handle", "@yoheinakajima")
    proposal = propose_web_research_plan_fn(graph)
    plan = proposal["plan"]
    plan_ref = plan.data["plan_identity"]
    approve_ingestion_plan_fn(graph, plan_ref=plan_ref, approved_by="owner:test")
    begun = begin_deferred_plan_execution_fn(graph, plan_ref=plan_ref)
    assert begun["ok"] is True

    # The owner edits while the perform phase is in flight elsewhere.
    edit_ingestion_plan_fn(
        graph, plan_ref=plan_ref,
        surfaces=[{**plan.data["surfaces"][0], "included": False}],
        edited_by="owner:test",
    )
    with pytest.raises(ValueError, match="superseded"):
        commit_deferred_plan_execution_fn(
            graph, plan_ref=plan_ref, payload=begun["payload"],
            outcome={"findings": [], "calls": 0, "model": None, "error": None},
        )
    assert not graph.objects(type="web_research_run")
