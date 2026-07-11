"""Deterministic fixtures for semantic attention observations."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parents[2]))

from activegraph import Graph, Runtime

from packs.attention import pack as attention_pack
from packs.attention.tools import record_interaction_batch_fn


def _build() -> Runtime:
    runtime = Runtime(Graph())
    runtime.load_pack(attention_pack)
    runtime.run_until_idle()
    return runtime


def run_semantic_batch_fixture() -> dict:
    runtime = _build()
    graph = runtime.graph
    result = record_interaction_batch_fn(
        graph,
        batch_id="batch_1",
        session_id="session_1",
        batch_sequence=0,
        client_id="mission-control",
        active_duration_ms=45_000,
        raw_event_count=4,
        flush_reason="view_change",
        observations=[
            {"subject_ref": "email_1", "subject_kind": "email", "signal_type": "impression"},
            {"subject_ref": "email_1", "subject_kind": "email", "signal_type": "opened"},
            {
                "subject_ref": "email_1",
                "subject_kind": "email",
                "signal_type": "active_dwell",
                "strength_milli": 750,
                "active_ms": 45_000,
            },
            {"subject_ref": "email_1", "subject_kind": "email", "signal_type": "replied"},
        ],
    )
    assert result["created"] is True
    assert len(result["observations"]) == 4
    batch = graph.objects(type="interaction_batch")[0]
    assert batch.data["privacy_mode"] == "semantic_only"

    retry = record_interaction_batch_fn(
        graph,
        batch_id="batch_1",
        session_id="session_1",
        batch_sequence=0,
        client_id="mission-control",
        observations=[],
    )
    assert retry["created"] is False
    assert len(graph.objects(type="attention_observation")) == 4
    return {"observations": 4, "idempotent": True}


def run_censored_absence_fixture() -> dict:
    runtime = _build()
    try:
        record_interaction_batch_fn(
            runtime.graph,
            batch_id="batch_bad",
            session_id="session_bad",
            batch_sequence=0,
            client_id="fixture",
            observations=[
                {"subject_ref": "email_1", "signal_type": "opened"},
                {"subject_ref": "email_2", "signal_type": "nonresponse_window"},
            ],
        )
    except ValueError as exc:
        assert "opportunity_id" in str(exc)
        assert not runtime.graph.objects(type="attention_observation")
        return {"failed_closed": True, "partial_writes": 0}
    raise AssertionError("nonresponse without opportunity must fail closed")


def run_all() -> bool:
    print("Attention Fixtures")
    print("=" * 60)
    print(f"  [1] semantic batch    PASS: {run_semantic_batch_fixture()}")
    print(f"  [2] censored absence PASS: {run_censored_absence_fixture()}")
    print("ALL PASS")
    return True


if __name__ == "__main__":
    try:
        ok = run_all()
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)
    sys.exit(0 if ok else 1)
