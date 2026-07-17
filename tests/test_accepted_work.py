"""Accepted-work projection conformance (Phase 5c closure — Gate B).

Owner-authorized work must be projectable from acceptance to terminal
outcome: queued, executing, blocked, or failed — with owner-readable
reasons — independent of any host scene, window, or process state. These
tests pin the neutral rows hosts render instead of inferring progress
from incidental objects.
"""

from __future__ import annotations

import pytest

from activegraph import Graph, Runtime

from packs.connector_control import pack as connector_control_pack
from packs.connector_control.accepted import accepted_plan_work_fn
from packs.connector_control.attempts import (
    begin_external_attempt_fn,
    mark_attempt_failed_fn,
    mark_attempt_performing_fn,
)
from packs.connector_control.plans import (
    approve_ingestion_plan_fn,
    bind_plan_execution_fn,
    propose_ingestion_plan_fn,
)


SURFACE = "mailbox:owner"
SERVICE = "fixture_mail"


@pytest.fixture
def runtime():
    rt = Runtime(Graph())
    rt.load_pack(connector_control_pack)
    rt.run_until_idle()
    yield rt


def _propose(graph, purpose="initial_backfill"):
    return propose_ingestion_plan_fn(
        graph,
        source_surface_id=SURFACE,
        service=SERVICE,
        account_ref="owner@example.com",
        family="conversation",
        window={"kind": "recent_days", "days": 30, "estimated_items": 40},
        derivation={
            "basis": "service_default", "summary": "fixture",
            "measurements": {}, "provenance": [],
        },
        surfaces=[],
        caps={"max_items": 100, "max_pages": 4},
        interpretation_stages=[],
        proposed_by="fixture.plan_proposer",
        purpose=purpose,
    )["plan"]


def _key(plan) -> str:
    return f"plan:{plan.data['plan_identity']}:v{plan.data['version']}"


def test_proposed_plans_are_not_accepted_work(runtime):
    _propose(runtime.graph)
    assert accepted_plan_work_fn(runtime.graph) == []


def test_approved_plan_is_queued_until_it_executes(runtime):
    graph = runtime.graph
    plan = _propose(graph)
    approve_ingestion_plan_fn(
        graph, plan_ref=plan.data["plan_identity"], approved_by="owner",
    )
    [row] = accepted_plan_work_fn(graph)
    assert row["state"] == "queued"
    assert row["kind"] == "ingestion_plan"
    assert row["purpose"] == "initial_backfill"
    assert row["reason"] is None

    bind_plan_execution_fn(
        graph, plan_ref=plan.data["plan_identity"],
        domain_run_id="fixture_run#1", source_surface_id=SURFACE,
    )
    [row] = accepted_plan_work_fn(graph)
    assert row["state"] == "executing"


def test_in_flight_attempt_reads_as_executing(runtime):
    graph = runtime.graph
    plan = _propose(graph)
    approve_ingestion_plan_fn(
        graph, plan_ref=plan.data["plan_identity"], approved_by="owner",
    )
    step = begin_external_attempt_fn(
        graph, kind="research_plan", idempotency_key=_key(plan),
        work_ref=plan.data["plan_identity"], payload={},
    )
    mark_attempt_performing_fn(graph, step["attempt_id"])
    [row] = accepted_plan_work_fn(graph)
    assert row["state"] == "executing"


def test_exhausted_attempts_surface_failed_with_the_reason(runtime):
    graph = runtime.graph
    plan = _propose(graph)
    approve_ingestion_plan_fn(
        graph, plan_ref=plan.data["plan_identity"], approved_by="owner",
    )
    # Burn the whole explicit attempt policy (default 2).
    for _ in range(2):
        step = begin_external_attempt_fn(
            graph, kind="research_plan", idempotency_key=_key(plan),
            work_ref=plan.data["plan_identity"], payload={},
        )
        assert step["action"] == "perform"
        mark_attempt_failed_fn(
            graph, step["attempt_id"], "provider unreachable: fixture down",
        )
    [row] = accepted_plan_work_fn(graph)
    assert row["state"] == "failed"
    assert "provider unreachable" in row["reason"]
    # After one failure (not exhausted) the row honestly reads queued-for-
    # retry with the prior error attached, never silent success.
    assert begin_external_attempt_fn(
        graph, kind="research_plan", idempotency_key=_key(plan),
        work_ref=plan.data["plan_identity"], payload={},
    )["action"] == "exhausted"


def test_blocked_attempt_surfaces_blocked_with_the_reason(runtime):
    graph = runtime.graph
    plan = _propose(graph)
    approve_ingestion_plan_fn(
        graph, plan_ref=plan.data["plan_identity"], approved_by="owner",
    )
    step = begin_external_attempt_fn(
        graph, kind="research_plan", idempotency_key=_key(plan),
        work_ref=plan.data["plan_identity"], payload={},
    )
    mark_attempt_failed_fn(
        graph, step["attempt_id"], "outcome exceeds inline limit", blocked=True,
    )
    [row] = accepted_plan_work_fn(graph)
    assert row["state"] == "blocked"
    assert "inline limit" in row["reason"]


def test_spent_retry_budget_after_failed_runs_reads_failed(runtime):
    """Every attempt committed a run and every run failed: the ledger will
    refuse the next begin, so the plan must surface failed — never linger
    'queued' while nothing can ever execute it."""
    from packs.connector_control.attempts import (
        mark_attempt_committed_fn,
        store_attempt_outcome_fn,
    )
    from packs.connector_control.plans import (
        bind_plan_execution_fn,
        settle_plan_for_run_fn,
    )

    graph = runtime.graph
    plan = _propose(graph)
    approve_ingestion_plan_fn(
        graph, plan_ref=plan.data["plan_identity"], approved_by="owner",
    )
    for round_number in range(2):
        step = begin_external_attempt_fn(
            graph, kind="research_plan", idempotency_key=_key(plan),
            work_ref=plan.data["plan_identity"], payload={},
        )
        assert step["action"] == "perform"
        mark_attempt_performing_fn(graph, step["attempt_id"])
        store_attempt_outcome_fn(graph, step["attempt_id"], {"ok": True})
        run_id = f"fixture_run#{round_number}"
        bind_plan_execution_fn(
            graph, plan_ref=plan.data["plan_identity"],
            domain_run_id=run_id, source_surface_id=SURFACE,
        )
        mark_attempt_committed_fn(graph, step["attempt_id"])
        assert settle_plan_for_run_fn(
            graph, domain_run_id=run_id, state="failed",
        ) == "approved"
    assert begin_external_attempt_fn(
        graph, kind="research_plan", idempotency_key=_key(plan),
        work_ref=plan.data["plan_identity"], payload={},
    )["action"] == "exhausted"
    [row] = accepted_plan_work_fn(graph)
    assert row["state"] == "failed"
    assert "retry budget is spent" in row["reason"]
    # And the terminality sweep turns it into an abandoned plan with the
    # same owner-readable reason.
    from packs.connector_control.accepted import settle_exhausted_plans_fn

    settled = settle_exhausted_plans_fn(graph)
    assert [entry["ref"] for entry in settled] == [plan.data["plan_identity"]]
    live = next(
        obj for obj in graph.objects(type="connector_ingestion_plan")
        if obj.data.get("plan_identity") == plan.data["plan_identity"]
    )
    assert live.data["status"] == "abandoned"
    assert "retry budget" in (live.data.get("metadata") or {}).get(
        "abandon_reason", ""
    )
    assert accepted_plan_work_fn(graph) == []


def test_two_purposes_project_as_two_independent_rows(runtime):
    graph = runtime.graph
    backfill = _propose(graph)
    approve_ingestion_plan_fn(
        graph, plan_ref=backfill.data["plan_identity"], approved_by="owner",
    )
    study = _propose(graph, purpose="comprehension")
    approve_ingestion_plan_fn(
        graph, plan_ref=study.data["plan_identity"], approved_by="owner",
    )
    rows = accepted_plan_work_fn(graph)
    assert {row["purpose"] for row in rows} == {
        "initial_backfill", "comprehension",
    }
    assert all(row["state"] == "queued" for row in rows)


def test_understanding_rows_cover_requests_reductions_and_campaigns():
    from packs.subject_synthesis import pack as synthesis_pack
    from packs.subject_synthesis.accepted import (
        accepted_understanding_work_fn,
    )

    rt = Runtime(Graph())
    rt.load_pack(synthesis_pack)
    rt.run_until_idle()
    graph = rt.graph
    graph.add_object("subject_synthesis_request", {
        "request_identity": "synthesis_request_fixture",
        "subject_ref": "owner",
        "reason": "confirmed fact landed",
        "status": "proposed",
        "run_id": None,
        "error": None,
        "metadata": {},
    })
    graph.add_object("comprehension_request", {
        "request_identity": "comprehension_request_fixture",
        "recipe_id": "gmail.sent-100",
        "service": "gmail",
        "source_surface_id": "surface_fixture",
        "plan_identity": "ingestion_plan_fixture",
        "status": "reducing",
        "requested_by": "owner:client",
        "counts": {"batches": 5, "batches_completed": 2, "items": 100},
        "coverage": {},
        "item_refs": [],
        "error": None,
        "metadata": {},
    })
    graph.add_object("comprehension_campaign", {
        "campaign_identity": "campaign_fixture",
        "subject_ref": "owner",
        "status": "open",
        "selected_affordances": ["gmail_sent_understanding"],
        "budgets": {}, "spent": {},
        "metadata": {},
    })
    rt.run_until_idle()
    rows = accepted_understanding_work_fn(graph)
    by_kind = {row["kind"]: row for row in rows}
    assert by_kind["synthesis_request"]["state"] == "queued"
    assert by_kind["comprehension_request"]["state"] == "executing"
    assert by_kind["comprehension_request"]["progress"]["batches_done"] == 2
    assert by_kind["campaign"]["state"] == "executing"
    assert by_kind["campaign"]["selected_affordances"] == [
        "gmail_sent_understanding",
    ]
