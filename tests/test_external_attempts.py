"""Durable external-work attempts (ADR 0041 as amended by ADR 0045).

The crash-window matrix the ledger exists for: crash before perform retries
explicitly; crash after perform and before commit commits the persisted
outcome without re-calling the provider; commit failure surfaces blocked
state; and nothing ever disappears from the operational projection.
"""

from activegraph import Graph, Runtime

from packs.connector_control import pack as connector_control_pack
from packs.connector_control.attempts import (
    ATTEMPT_INLINE_LIMIT_BYTES,
    attempt_ledger_for_key_fn,
    begin_external_attempt_fn,
    mark_attempt_committed_fn,
    mark_attempt_failed_fn,
    mark_attempt_performing_fn,
    pending_commit_attempts_fn,
    project_external_work_attempts_fn,
    store_attempt_outcome_fn,
)


def _graph():
    graph = Graph()
    runtime = Runtime(graph)
    runtime.load_pack(connector_control_pack)
    runtime.run_until_idle()
    return graph, runtime


KEY = "research_plan:plan_abc:v1"


def test_happy_path_records_every_phase_and_commits_once():
    graph, _ = _graph()
    step = begin_external_attempt_fn(
        graph, kind="research_plan", idempotency_key=KEY,
        work_ref="plan_abc", payload={"queries": ["\"Yohei Nakajima\""]},
    )
    assert step["action"] == "perform"
    assert step["payload"] == {"queries": ["\"Yohei Nakajima\""]}
    mark_attempt_performing_fn(graph, step["attempt_id"])
    assert graph.get_object(step["attempt_id"]).data["phase"] == "performing"
    stored = store_attempt_outcome_fn(
        graph, step["attempt_id"], {"findings": [{"claim": "x", "url": "https://a.test"}]}
    )
    assert stored["ok"] is True
    assert graph.get_object(step["attempt_id"]).data["phase"] == "commit_pending"
    mark_attempt_committed_fn(graph, step["attempt_id"])
    assert graph.get_object(step["attempt_id"]).data["phase"] == "committed"
    # The ledger is the audit trail: one attempt, no failures.
    ledger = attempt_ledger_for_key_fn(graph, KEY)
    assert [row["phase"] for row in ledger] == ["committed"]


def test_crash_before_perform_retries_explicitly_and_visibly():
    graph, _ = _graph()
    first = begin_external_attempt_fn(
        graph, kind="research_plan", idempotency_key=KEY,
        work_ref="plan_abc", payload={"queries": ["q"]},
    )
    mark_attempt_performing_fn(graph, first["attempt_id"])
    # Process dies here: no outcome was ever persisted. A new process begins
    # the same key; the stale row fails visibly and a fresh attempt opens.
    second = begin_external_attempt_fn(
        graph, kind="research_plan", idempotency_key=KEY,
        work_ref="plan_abc", payload={"queries": ["q"]},
    )
    assert second["action"] == "perform"
    assert second["attempt_number"] == 2
    ledger = attempt_ledger_for_key_fn(graph, KEY)
    assert [row["phase"] for row in ledger] == ["failed", "prepared"]
    assert ledger[0]["error"] == "superseded_by_retry_after_crash"


def test_crash_after_perform_before_commit_never_recalls_the_provider():
    graph, _ = _graph()
    step = begin_external_attempt_fn(
        graph, kind="synthesis", idempotency_key="synthesis:req1",
        work_ref="req1", payload={"facts": [1, 2]},
    )
    mark_attempt_performing_fn(graph, step["attempt_id"])
    store_attempt_outcome_fn(graph, step["attempt_id"], {"text": "the outcome"})
    # Process dies between outcome persistence and commit. Restart:
    resumed = begin_external_attempt_fn(
        graph, kind="synthesis", idempotency_key="synthesis:req1",
        work_ref="req1", payload={"facts": [1, 2]},
    )
    assert resumed["action"] == "commit"
    assert resumed["outcome"] == {"text": "the outcome"}
    assert resumed["payload"] == {"facts": [1, 2]}
    # And the restart-recovery scan finds it too.
    pending = pending_commit_attempts_fn(graph)
    assert [row["idempotency_key"] for row in pending] == ["synthesis:req1"]
    mark_attempt_committed_fn(graph, resumed["attempt_id"])
    assert pending_commit_attempts_fn(graph) == []


def test_commit_failure_blocks_and_the_policy_bounds_retries():
    graph, _ = _graph()
    step = begin_external_attempt_fn(
        graph, kind="extraction", idempotency_key="extraction:req9",
        work_ref="req9", payload={}, max_attempts=2,
    )
    mark_attempt_failed_fn(graph, step["attempt_id"], "provider timeout")
    second = begin_external_attempt_fn(
        graph, kind="extraction", idempotency_key="extraction:req9",
        work_ref="req9", payload={}, max_attempts=2,
    )
    assert second["action"] == "perform" and second["attempt_number"] == 2
    mark_attempt_failed_fn(graph, second["attempt_id"], "commit raised", blocked=True)
    third = begin_external_attempt_fn(
        graph, kind="extraction", idempotency_key="extraction:req9",
        work_ref="req9", payload={}, max_attempts=2,
    )
    assert third["action"] == "blocked"
    assert third["reason"] == "commit raised"
    # The failure can never disappear: the projection carries it.
    projection = project_external_work_attempts_fn(graph)
    assert projection["failed_or_blocked"] == 1
    [row] = [r for r in projection["attempts"] if r["idempotency_key"] == "extraction:req9"]
    assert row["retried"] is True and row["had_failure"] is True


def test_exhausted_attempts_surface_instead_of_looping():
    graph, _ = _graph()
    for expected_number in (1, 2):
        step = begin_external_attempt_fn(
            graph, kind="research_plan", idempotency_key=KEY,
            work_ref="plan_abc", payload={}, max_attempts=2,
        )
        assert step["action"] == "perform"
        assert step["attempt_number"] == expected_number
        mark_attempt_failed_fn(graph, step["attempt_id"], "boom")
    final = begin_external_attempt_fn(
        graph, kind="research_plan", idempotency_key=KEY,
        work_ref="plan_abc", payload={}, max_attempts=2,
    )
    assert final["action"] == "exhausted"
    assert final["attempts"] == 2


def test_oversized_or_secret_bearing_material_is_refused_not_truncated():
    graph, _ = _graph()
    huge = {"blob": "x" * (ATTEMPT_INLINE_LIMIT_BYTES + 1)}
    step = begin_external_attempt_fn(
        graph, kind="synthesis", idempotency_key="synthesis:big",
        work_ref="big", payload=huge,
    )
    assert step["action"] == "blocked"
    assert step["reason"] == "payload_exceeds_inline_limit"

    ok = begin_external_attempt_fn(
        graph, kind="synthesis", idempotency_key="synthesis:secret",
        work_ref="secret", payload={},
    )
    store_attempt_outcome_fn(
        graph, ok["attempt_id"],
        {"note": "key sk-abc123def456ghi789jkl012mno345pqr678 leaked"},
    )
    stored = graph.get_object(ok["attempt_id"]).data["outcome_json"]
    assert "sk-abc123def456ghi789jkl012mno345pqr678" not in stored
    assert "[REDACTED:api_key]" in stored
