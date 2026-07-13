"""Neutral connector control-plane and five-family contract tests."""

from __future__ import annotations

import pytest

from activegraph import Graph, Runtime

from packs.connector_control import pack
from packs.connector_control.operational import (
    ConnectorOperationalMeasurement,
    operational_budget_violations,
)
from packs.connector_control.tools import (
    project_connector_control_plane_fn,
    record_connector_binding_fn,
    record_connector_learning_delta_fn,
    record_connector_native_view_fn,
    record_connector_run_observation_fn,
)


@pytest.fixture
def graph():
    runtime = Runtime(Graph())
    runtime.load_pack(pack)
    return runtime.graph


def test_route_change_preserves_surface_identity_and_route_history(graph):
    first = record_connector_binding_fn(
        graph, source_surface_id="mail:owner", service="gmail",
        account_ref="owner@example.com", family="conversation",
        active_route="composio", domain_run_type="gmail_sync_run",
    )
    second = record_connector_binding_fn(
        graph, source_surface_id="mail:owner", service="gmail",
        account_ref="owner@example.com", family="conversation",
        active_route="native", domain_run_type="gmail_sync_run",
    )
    assert first["binding"].id == second["binding"].id
    assert second["binding"].data["routes"] == ["composio", "native"]
    assert second["binding"].data["active_route"] == "native"


def test_domain_run_adapter_exposes_safe_status_and_event_refs(graph):
    anchor = record_connector_binding_fn(
        graph, source_surface_id="mail:owner", service="gmail",
        account_ref="owner@example.com", family="conversation",
        active_route="composio", domain_run_type="gmail_sync_run",
    )["binding"]
    attempt_event = graph.events[-1].id
    record_connector_run_observation_fn(
        graph, domain_run_id="gmail-run-1", source_surface_id="mail:owner",
        service="gmail", account_ref="owner@example.com", family="conversation",
        route="composio", state="running", phase="acquiring", mode="backfill",
        source_event_id=attempt_event, attempt=True,
        bounds={"max_items": 250}, counts={"imported": 0},
        cursor={"position_kind": "history", "has_position": False,
                "advanced": False, "coverage": "unknown"},
    )
    terminal_source = graph.events[-1].id
    record_connector_run_observation_fn(
        graph, domain_run_id="gmail-run-1", source_surface_id="mail:owner",
        service="gmail", account_ref="owner@example.com", family="conversation",
        route="composio", state="partial", phase="sample_ready", mode="backfill",
        source_event_id=terminal_source,
        bounds={"max_items": 250}, counts={"imported": 250},
        cursor={"position_kind": "history", "has_position": True,
                "advanced": True, "coverage": "bounded"},
        maintenance_mode="manual", manual_refresh_available=True,
        next_sync_available=True,
    )
    row = project_connector_control_plane_fn(graph)["runs"][0]
    assert row["domain_run_id"] == "gmail-run-1"
    assert row["state"] == "partial"
    assert row["last_attempt"]["event_id"] == attempt_event
    assert row["last_success"]["event_id"] == terminal_source
    assert row["cursor"] == {
        "position_kind": "history", "has_position": True,
        "advanced": True, "coverage": "bounded",
    }
    assert "token" not in str(row["cursor"]).lower()
    assert anchor.id


@pytest.mark.parametrize(
    ("family", "data"),
    [
        ("conversation", {"threads": [], "total_count": 0}),
        ("schedule", {"occurrences": [], "total_count": 0}),
        ("records", {"columns": [], "rows": [], "total_count": 0}),
        ("documents", {"items": [], "total_count": 0}),
        ("telemetry", {"points": [], "unit": "count"}),
    ],
)
def test_all_five_family_native_shapes_validate(graph, family, data):
    result = record_connector_native_view_fn(
        graph, source_surface_id=f"fixture:{family}", service="fixture",
        family=family, state="empty", data=data,
    )
    assert result["view"].data["family"] == family
    assert result["view"].data["state"] == "empty"


def test_family_contract_rejects_provider_shaped_unknown_fields(graph):
    with pytest.raises(ValueError):
        record_connector_native_view_fn(
            graph, source_surface_id="mail:owner", service="gmail",
            family="conversation", state="ready",
            data={"messages": [{"gmailMessageId": "provider-leak"}]},
        )


def test_learning_delta_is_counts_plus_bounded_refs(graph):
    result = record_connector_learning_delta_fn(
        graph, domain_run_id="run-1", source_surface_id="mail:owner",
        service="gmail", family="conversation", status="complete",
        evidence={"created": 2, "updated": 1, "deleted": 1},
        annotation_coverage={"entity_mention": 2},
        candidates={"task": {"proposed": 1, "promoted": 0}},
        refs=["evt_1", "evt_1", "evt_2"],
    )
    assert result["delta"].data["refs"] == ["evt_1", "evt_2"]
    assert result["delta"].data["candidates"]["task"]["proposed"] == 1


def test_operational_policy_is_versioned_and_enforces_every_measurement():
    runtime = Runtime(Graph())
    runtime.load_pack(pack)
    runtime.run_until_idle()
    [policy] = list(runtime.graph.objects(type="connector_operational_policy"))
    assert policy.data["policy_identity"] == "connector-operational@0.2.0"
    assert policy.data["version"] == 2
    assert policy.data["max_events_per_evidence"] == 100
    assert policy.data["max_annotations_per_evidence"] == 20
    assert policy.data["max_acquisition_items"] == 250
    assert policy.data["max_acquisition_pages"] == 10

    measurement = ConnectorOperationalMeasurement(
        domain_run_id="fixture-run",
        evidence_items=250,
        durable_events=25_000,
        events_per_evidence=100,
        annotations=5_000,
        max_annotations_for_one_evidence=20,
        behavior_firings=6_250,
        behavior_firings_per_evidence=25,
        provider_calls=10,
        artifact_bytes=64 * 1024 * 1024,
        max_queue_depth=5_000,
        acknowledgement_ms=1_000,
        first_progress_ms=2_000,
        projection_read_p95_ms=500,
        max_unyielded_ms=500,
    )
    assert operational_budget_violations(measurement) == []
    over = measurement.model_copy(update={"events_per_evidence": 100.01})
    assert operational_budget_violations(over) == [
        "events_per_evidence: 100.01 > 100"
    ]
