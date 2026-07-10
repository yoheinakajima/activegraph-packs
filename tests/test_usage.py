"""P2 acceptance contract for neutral, explicit-horizon usage projections."""

from __future__ import annotations

import re
from typing import Any

import pytest
from activegraph import Event, Graph, Runtime, TickingClock

from packs.usage import UsageSettings, pack as usage_pack
from packs.usage.tools import (
    connect_surface_fn,
    get_coverage_fn,
    get_settlement_fn,
    list_surfaces_fn,
    project_usage_fn,
    set_surface_status_fn,
)


def _graph() -> Graph:
    return Graph(clock=TickingClock("2024-01-01T00:00:00Z", step_seconds=1))


def _runtime() -> Runtime:
    runtime = Runtime(_graph())
    runtime.load_pack(usage_pack, settings=UsageSettings())
    return runtime


def _horizon(graph: Graph) -> str:
    assert graph.events, "fixture must have an explicit event horizon"
    return graph.events[-1].id


def _emit(graph: Graph, event_type: str, payload: dict[str, Any]) -> str:
    event = Event(
        id=graph.ids.event(),
        type=event_type,
        payload=payload,
        actor="usage.acceptance",
        timestamp=graph.clock.now(),
    )
    graph.emit(event)
    return event.id


def _emit_evidence(
    graph: Graph,
    surface_id: str,
    identity: str,
    *,
    category: str,
    path: str,
    provider_time: str | None = "2024-01-01T00:00:00+00:00",
    is_fixture: bool = False,
    revision_number: int = 1,
) -> str:
    return _emit(
        graph,
        "source.event_ingested",
        {
            "evidence_identity": identity,
            "evidence_id": f"activity_evidence#{identity}",
            "revision_id": f"revision:{identity}:{revision_number}",
            "revision_number": revision_number,
            "source_surface_id": surface_id,
            "provider_item_id": identity,
            "source_ref": f"fixture://{surface_id}/{identity}",
            "content_hash": f"hash:{identity}:{revision_number}",
            "provider_time": provider_time,
            "source_category": category,
            "connection_path": path,
            "importer_id": "usage_acceptance",
            "importer_version": "0.1.0",
            "is_fixture": is_fixture,
            "replay_complete": True,
            "invalidated": False,
        },
    )


def _settled_events(graph: Graph, surface_id: str) -> list[Event]:
    return [
        event
        for event in graph.events
        if event.type == "source.settled"
        and (event.payload or {}).get("surface_id") == surface_id
        and (event.payload or {}).get("gate_id") == "usage.category.default"
        and (event.payload or {}).get("gate_version") == 1
    ]


def _replay(events: list[Event]) -> Graph:
    replayed = _graph()
    for event in events:
        replayed._replay_event(event)
    replayed.ids.reseed_from_events(events)
    return replayed


def test_connection_alone_never_settles_or_contributes() -> None:
    graph = _graph()
    result = connect_surface_fn(
        graph,
        "surface_connection_only",
        "communication",
        provider={"name": "Example"},
        path="native",
        acquisition_mode="live",
    )
    horizon = result["connection_event_id"]

    settlement = get_settlement_fn(graph, horizon, "surface_connection_only")
    assert settlement["status"] == "connected"
    assert settlement["passed"] is False
    assert settlement["settled_event_id"] is None
    assert _settled_events(graph, "surface_connection_only") == []


def test_default_gate_passes_by_volume_or_provider_time_coverage() -> None:
    graph = _graph()
    connect_surface_fn(graph, "surface_volume", "local_knowledge", path="local")
    connect_surface_fn(graph, "surface_coverage", "ai_activity", path="export")
    for index in range(25):
        _emit_evidence(
            graph,
            "surface_volume",
            f"volume-{index:02d}",
            category="local_knowledge",
            path="local",
        )
    _emit_evidence(
        graph,
        "surface_coverage",
        "coverage-start",
        category="ai_activity",
        path="export",
        provider_time="2024-01-01T12:00:00+00:00",
    )
    _emit_evidence(
        graph,
        "surface_coverage",
        "coverage-end",
        category="ai_activity",
        path="export",
        provider_time="2024-01-04T12:00:00+00:00",
    )
    horizon = _horizon(graph)

    volume = get_settlement_fn(graph, horizon, "surface_volume")
    coverage = get_settlement_fn(graph, horizon, "surface_coverage")
    assert volume["status"] == "settled"
    assert volume["passed_by"] == "volume"
    assert coverage["status"] == "settled"
    assert coverage["passed_by"] == "coverage"
    stats = get_coverage_fn(graph, horizon)["by_surface"]
    assert stats["surface_volume"]["unique_evidence"] == 25
    assert stats["surface_coverage"]["coverage_days"] == 3


def test_reconnect_and_reimport_never_duplicate_settled_tuple() -> None:
    runtime = _runtime()
    graph = runtime.graph
    connect_surface_fn(graph, "surface_retry", "local_knowledge", path="local")
    for index in range(25):
        _emit_evidence(
            graph,
            "surface_retry",
            f"retry-{index:02d}",
            category="local_knowledge",
            path="local",
        )
    runtime.run_until_idle()
    assert len(_settled_events(graph, "surface_retry")) == 1

    reconnect = connect_surface_fn(
        graph, "surface_retry", "local_knowledge", path="local"
    )
    assert reconnect["created"] is False
    for index in range(25):
        _emit_evidence(
            graph,
            "surface_retry",
            f"retry-{index:02d}",
            category="local_knowledge",
            path="local",
        )
    runtime.run_until_idle()
    horizon = _horizon(graph)

    assert len(_settled_events(graph, "surface_retry")) == 1
    records = [
        item
        for item in graph.objects(type="settlement_record")
        if item.data.get("source_surface_id") == "surface_retry"
        and item.data.get("gate_id") == "usage.category.default"
        and item.data.get("gate_version") == 1
    ]
    assert len(records) == 1
    assert get_settlement_fn(graph, horizon, "surface_retry")["status"] == "settled"


def test_stale_and_revoked_are_horizon_deterministic_and_log_replayable() -> None:
    graph = _graph()
    connect_surface_fn(graph, "surface_lifecycle", "ai_activity", path="export")
    _emit_evidence(
        graph,
        "surface_lifecycle",
        "life-start",
        category="ai_activity",
        path="export",
        provider_time="2024-02-01T00:00:00+00:00",
    )
    settled_horizon = _emit_evidence(
        graph,
        "surface_lifecycle",
        "life-end",
        category="ai_activity",
        path="export",
        provider_time="2024-02-04T00:00:00+00:00",
    )
    stale_horizon = set_surface_status_fn(
        graph, "surface_lifecycle", "stale", reason="source access expired"
    )["event_id"]
    revoked_horizon = set_surface_status_fn(
        graph, "surface_lifecycle", "revoked", reason="owner revoked access"
    )["event_id"]

    expected = {
        settled_horizon: "settled",
        stale_horizon: "stale",
        revoked_horizon: "revoked",
    }
    original_events = list(graph.events)
    replayed = _replay(original_events)
    for horizon, status in expected.items():
        original_projection = project_usage_fn(graph, horizon)
        replayed_projection = project_usage_fn(replayed, horizon)
        assert original_projection == replayed_projection
        settlement = get_settlement_fn(graph, horizon, "surface_lifecycle")
        assert settlement["status"] == status


def test_unknown_category_is_rejected_loudly() -> None:
    graph = _graph()
    with pytest.raises(ValueError, match="unknown source category"):
        connect_surface_fn(graph, "surface_unknown", "other", path="native")
    assert not list(graph.objects(type="connection_surface"))


def test_fixture_evidence_is_visible_but_excluded_from_gate_totals() -> None:
    graph = _graph()
    connect_surface_fn(graph, "surface_fixture", "local_knowledge", path="local")
    for index in range(25):
        _emit_evidence(
            graph,
            "surface_fixture",
            f"fixture-{index:02d}",
            category="local_knowledge",
            path="local",
            is_fixture=True,
        )
    horizon = _horizon(graph)

    settlement = get_settlement_fn(graph, horizon, "surface_fixture")
    stats = get_coverage_fn(graph, horizon)["by_surface"]["surface_fixture"]
    assert settlement["status"] == "connected"
    assert settlement["passed"] is False
    assert stats["unique_evidence"] == 0
    assert stats["fixture_evidence"] == 25
    assert stats["visible_evidence_revisions"] == 25


def test_pack_surface_contains_neutral_facts_and_no_game_vocabulary() -> None:
    forbidden = re.compile(r"\b(score|points?|badges?|levels?)\b", re.IGNORECASE)
    surfaces: list[tuple[str, str]] = [(usage_pack.name, usage_pack.description)]
    surfaces.extend((item.name, item.description) for item in usage_pack.object_types)
    surfaces.extend((item.name, item.description) for item in usage_pack.relation_types)
    surfaces.extend((item.name, item.description) for item in usage_pack.tools)
    surfaces.extend((item.name, item.fn.__doc__ or "") for item in usage_pack.behaviors)
    violations = [name for name, description in surfaces if forbidden.search(f"{name} {description}")]
    assert violations == []
