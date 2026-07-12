"""Conversation + schedule conformance for the neutral control plane."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parents[2]))

from activegraph import Graph, Runtime

from packs.connector_control import pack
from packs.connector_control.tools import (
    project_connector_control_plane_fn,
    project_connector_learning_deltas_fn,
    record_connector_binding_fn,
    record_connector_learning_delta_fn,
    record_connector_native_view_fn,
    record_connector_run_observation_fn,
)


def run_fixture() -> dict:
    runtime = Runtime(Graph())
    runtime.load_pack(pack)
    graph = runtime.graph

    record_connector_binding_fn(
        graph, source_surface_id="gmail:owner", service="gmail",
        account_ref="owner@example.com", family="conversation",
        active_route="composio", domain_run_type="gmail_sync_run",
        routes=["composio"], maintenance_mode="manual",
        manual_refresh_available=True,
    )
    attempt = graph.events[-1].id
    record_connector_run_observation_fn(
        graph, domain_run_id="gmail-run-fixture", source_surface_id="gmail:owner",
        service="gmail", account_ref="owner@example.com", family="conversation",
        route="composio", state="partial", phase="sample_ready", mode="backfill",
        source_event_id=attempt, attempt=True, bounds={"max_items": 250},
        counts={"imported": 250, "deleted": 0},
        cursor={"position_kind": "history", "has_position": True,
                "advanced": True, "coverage": "bounded"},
        maintenance_mode="manual", manual_refresh_available=True,
        next_sync_available=True,
    )
    record_connector_learning_delta_fn(
        graph, domain_run_id="gmail-run-fixture", source_surface_id="gmail:owner",
        service="gmail", family="conversation", status="partial",
        evidence={"created": 250, "updated": 0, "deleted": 0},
        annotation_coverage={"entity_mention": 250}, refs=[attempt],
    )
    record_connector_native_view_fn(
        graph, source_surface_id="gmail:owner", service="gmail",
        family="conversation", state="partial",
        data={"threads": [], "total_count": 0}, refs=[attempt],
        service_extensions={"gmail": {"labels_available": True}},
    )

    # A second, non-conversation family proves the contract is not Gmail-shaped.
    record_connector_binding_fn(
        graph, source_surface_id="calendar:fixture", service="fixture_calendar",
        account_ref="calendar-owner", family="schedule", active_route="native",
        domain_run_type="fixture_schedule_run", maintenance_mode="external",
    )
    schedule_event = graph.events[-1].id
    record_connector_run_observation_fn(
        graph, domain_run_id="schedule-run-fixture", source_surface_id="calendar:fixture",
        service="fixture_calendar", account_ref="calendar-owner", family="schedule",
        route="native", state="succeeded", phase="served", mode="window",
        source_event_id=schedule_event, attempt=True,
        bounds={"window_days": 14}, counts={"occurrences": 2},
        cursor={"position_kind": "window", "has_position": True,
                "advanced": True, "coverage": "current"},
        maintenance_mode="external", next_sync_available=True,
    )
    record_connector_native_view_fn(
        graph, source_surface_id="calendar:fixture", service="fixture_calendar",
        family="schedule", state="ready",
        data={
            "occurrences": [
                {"occurrence_ref": "event:1", "title": "Planning",
                 "start": "2026-07-13T09:00:00-07:00", "refs": [schedule_event]},
                {"occurrence_ref": "event:2", "title": "Review",
                 "start": "2026-07-14T10:00:00-07:00", "refs": [schedule_event]},
            ],
            "window_start": "2026-07-12", "window_end": "2026-07-26",
            "total_count": 2,
        },
    )

    control = project_connector_control_plane_fn(graph)
    deltas = project_connector_learning_deltas_fn(graph)
    assert {row["family"] for row in control["bindings"]} == {"conversation", "schedule"}
    assert {row["family"] for row in control["native_views"]} == {"conversation", "schedule"}
    assert len(control["runs"]) == 2
    assert control["runs"][0]["current_work_ref"]
    assert deltas["deltas"][0]["evidence"]["created"] == 250
    return {"families": 2, "runs": 2, "learning_deltas": 1}


if __name__ == "__main__":
    try:
        print(f"Connector Control Fixtures PASS: {run_fixture()}")
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)
