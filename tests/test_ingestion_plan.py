"""Neutral ingestion-plan lifecycle conformance (ADR 0039 / D059).

Family-generic by construction: these tests run the full lifecycle against a
schedule-family fixture service with no Gmail machinery, so a second family
(calendar) inherits proposal, supersession, ceilings, prediction, binding,
and settlement with zero new code.
"""

from __future__ import annotations

import pytest

from activegraph import Graph, Runtime

from packs.connector_control import pack as connector_control_pack
from packs.connector_control.plans import (
    abandon_ingestion_plan_fn,
    approve_ingestion_plan_fn,
    bind_plan_execution_fn,
    current_plan_for_surface_fn,
    edit_ingestion_plan_fn,
    execute_ingestion_plan_fn,
    propose_ingestion_plan_fn,
    register_ingestion_plan_executor,
    unregister_ingestion_plan_executor,
)
from packs.connector_control.tools import (
    project_ingestion_plans_fn,
    record_connector_run_observation_fn,
)


SURFACE = "calendar:owner"


@pytest.fixture
def runtime():
    rt = Runtime(Graph())
    rt.load_pack(connector_control_pack)
    rt.run_until_idle()
    yield rt
    unregister_ingestion_plan_executor("fixture_calendar")


def _propose(graph, **overrides):
    payload = {
        "source_surface_id": SURFACE,
        "service": "fixture_calendar",
        "account_ref": "owner@example.com",
        "family": "schedule",
        "window": {"kind": "recent_days", "days": 60, "estimated_items": 40},
        "derivation": {
            "basis": "measured_topology",
            "summary": "calendar spans 2024-01 to now; proposing 60 days ≈ 40 events",
            "measurements": {"events_total": 512},
            "provenance": ["probe_call_1"],
        },
        "surfaces": [{
            "surface_ref": "calendar:primary",
            "label": "primary",
            "included": True,
            "expectation": {"estimated_richness": "medium", "confidence": 0.7},
        }],
        "caps": {"max_items": 100, "max_pages": 4},
        "interpretation_stages": ["schedule.mapper@0.1.0"],
        "proposed_by": "fixture_calendar.plan_proposer",
    }
    payload.update(overrides)
    return propose_ingestion_plan_fn(graph, **payload)


def test_prediction_is_recorded_before_any_verdict_and_learns_per_family(runtime):
    graph = runtime.graph
    plan = _propose(graph)["plan"]
    # ADR 0018: the acceptance prediction exists at proposal time, before any
    # owner verdict can exist, and names its deterministic basis.
    assert plan.data["predicted_verdict"] == "approved_as_proposed"
    assert plan.data["verdict"] is None
    assert plan.data["prediction_basis"]["scope"] == "schedule"
    assert plan.data["prediction_basis"]["prior_total"] == 0

    edited = edit_ingestion_plan_fn(
        graph, plan_ref=plan.data["plan_identity"],
        caps={"max_items": 50}, edited_by="owner",
    )["plan"]
    original = graph.get_object(plan.id)
    # The owner's edit is the verdict evidence on the proposal it replaced.
    assert original.data["verdict"] == "edited"
    assert original.data["verdict_actor"] == "owner"
    assert original.data["status"] == "superseded"
    assert edited.data["prediction_basis"]["prior_total"] == 1
    assert edited.data["prediction_basis"]["prior_verdicts"]["edited"] == 1

    approve_ingestion_plan_fn(
        graph, plan_ref=edited.data["plan_identity"], approved_by="owner"
    )
    settled = graph.get_object(edited.id)
    assert settled.data["verdict"] == "approved_as_proposed"

    # A later same-family proposal reads both verdicts.
    abandon_ingestion_plan_fn(
        graph, plan_ref=edited.data["plan_identity"], actor="owner"
    )
    replacement = _propose(graph)["plan"]
    basis = replacement.data["prediction_basis"]
    assert basis["prior_total"] == 2
    assert basis["prior_verdicts"] == {
        "approved_as_proposed": 1, "edited": 1, "abandoned": 0,
    }


def test_lowering_is_free_and_raising_past_the_ceiling_names_the_escalation(runtime):
    graph = runtime.graph
    plan = _propose(graph)["plan"]
    lowered = edit_ingestion_plan_fn(
        graph, plan_ref=plan.data["plan_identity"],
        caps={"max_items": 5, "max_pages": 1}, edited_by="owner",
    )["plan"]
    assert lowered.data["caps"]["max_items"] == 5
    assert lowered.data["version"] == 2

    with pytest.raises(ValueError) as excinfo:
        edit_ingestion_plan_fn(
            graph, plan_ref=lowered.data["plan_identity"],
            caps={"max_items": 100_000}, edited_by="owner",
        )
    message = str(excinfo.value)
    assert "exceeds the operational policy ceiling" in message
    assert "connector-operational@0.3.0" in message
    assert "escalation" in message
    # The rejected edit left no new version behind.
    head = current_plan_for_surface_fn(graph, SURFACE)
    assert head.id == lowered.id
    assert head.data["status"] == "proposed"

    with pytest.raises(ValueError, match="operational policy ceiling"):
        _propose(graph, caps={"max_items": 100_000, "max_pages": 4})


def test_superseded_plan_can_never_execute_and_runs_bind_the_approved_version(runtime):
    graph = runtime.graph
    started: list[str] = []

    def executor(graph_, plan_):
        run = record_connector_run_observation_fn(
            graph_,
            domain_run_id=f"calendar-run-{len(started) + 1}",
            source_surface_id=SURFACE,
            service="fixture_calendar",
            account_ref="owner@example.com",
            family="schedule",
            route="native",
            state="running",
            phase="acquiring",
            mode="backfill",
        )["observation"]
        started.append(run.data["domain_run_id"])
        return {"run_id": run.data["domain_run_id"]}

    register_ingestion_plan_executor("fixture_calendar", executor)

    v1 = _propose(graph)["plan"]
    v2 = edit_ingestion_plan_fn(
        graph, plan_ref=v1.data["plan_identity"],
        caps={"max_items": 25}, edited_by="owner",
    )["plan"]

    # The superseded version is dead on every path.
    with pytest.raises(ValueError, match="never execute"):
        bind_plan_execution_fn(
            graph, plan_ref=v1.data["plan_identity"],
            domain_run_id="calendar-run-x", source_surface_id=SURFACE,
        )
    with pytest.raises(ValueError, match="only a proposed plan"):
        approve_ingestion_plan_fn(
            graph, plan_ref=v1.data["plan_identity"], approved_by="owner"
        )
    with pytest.raises(ValueError, match="approve the current version"):
        execute_ingestion_plan_fn(graph, plan_ref=v2.data["plan_identity"])

    approve_ingestion_plan_fn(
        graph, plan_ref=v2.data["plan_identity"], approved_by="owner"
    )
    result = execute_ingestion_plan_fn(graph, plan_ref=v2.data["plan_identity"])
    assert result["ok"] is True
    assert started == ["calendar-run-1"]
    executing = graph.get_object(v2.id)
    assert executing.data["status"] == "executing"
    assert executing.data["domain_run_id"] == "calendar-run-1"

    # Re-execution while bound is idempotent, not a second run.
    again = execute_ingestion_plan_fn(graph, plan_ref=v2.data["plan_identity"])
    assert again["already_executing"] is True
    assert started == ["calendar-run-1"]

    # Terminal observation settles the plan through the neutral behavior.
    record_connector_run_observation_fn(
        graph,
        domain_run_id="calendar-run-1",
        source_surface_id=SURFACE,
        service="fixture_calendar",
        account_ref="owner@example.com",
        family="schedule",
        route="native",
        state="succeeded",
        phase="served",
        mode="backfill",
    )
    runtime.run_until_idle()
    assert graph.get_object(v2.id).data["status"] == "fulfilled"

    rows = project_ingestion_plans_fn(graph)["plans"]
    assert [row["version"] for row in rows] == [1, 2]
    assert rows[0]["superseded_by"] == rows[1]["plan_identity"]


def test_execution_without_approval_or_plan_fails_loud(runtime):
    graph = runtime.graph
    plan = _propose(graph)["plan"]
    with pytest.raises(ValueError, match="approve the current version"):
        execute_ingestion_plan_fn(graph, plan_ref=plan.data["plan_identity"])
    with pytest.raises(ValueError, match="does not exist"):
        execute_ingestion_plan_fn(graph, plan_ref="ingestion_plan_missing")
    with pytest.raises(ValueError, match="does not exist"):
        bind_plan_execution_fn(
            graph, plan_ref="ingestion_plan_missing",
            domain_run_id="run", source_surface_id=SURFACE,
        )


def test_identical_reproposal_is_idempotent_while_unanswered(runtime):
    graph = runtime.graph
    first = _propose(graph)
    second = _propose(graph)
    assert first["created"] is True
    assert second["created"] is False
    assert second["plan"].id == first["plan"].id

    # A changed derivation supersedes the unanswered proposal instead.
    third = _propose(graph, window={"kind": "recent_days", "days": 90, "estimated_items": 60})
    assert third["created"] is True
    assert graph.get_object(first["plan"].id).data["status"] == "superseded"
    assert third["plan"].data["version"] == 2
