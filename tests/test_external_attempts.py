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


def test_committed_key_retries_are_bounded_by_the_attempt_policy():
    """Released work legitimately re-begins its key after a committed
    attempt (a failed plan returns to approved and retries). The contract:
    the fall-through opens the NEXT attempt, and the explicit policy — not
    a process-local set — exhausts the loop, so a caller that re-offers
    committed work costs at most (max_attempts - 1) extra performs."""
    from packs.connector_control.attempts import (
        begin_external_attempt_fn,
        mark_attempt_committed_fn,
        mark_attempt_performing_fn,
        store_attempt_outcome_fn,
    )

    graph, _runtime = _graph()

    def _one_cycle(expected_number):
        step = begin_external_attempt_fn(
            graph, kind="extraction", idempotency_key="k-done",
            work_ref="req#1", payload={"n": 1},
        )
        assert step["action"] == "perform"
        assert step["attempt_number"] == expected_number
        mark_attempt_performing_fn(graph, step["attempt_id"])
        store_attempt_outcome_fn(graph, step["attempt_id"], {"ok": True})
        mark_attempt_committed_fn(graph, step["attempt_id"])

    _one_cycle(1)
    _one_cycle(2)
    exhausted = begin_external_attempt_fn(
        graph, kind="extraction", idempotency_key="k-done",
        work_ref="req#1", payload={"n": 1},
    )
    assert exhausted["action"] == "exhausted"
    assert exhausted["attempts"] == 2


def test_conversation_native_contract_accepts_attention_refs():
    """Live-run regression: threads gained learned-salience attention_refs
    (ADR 0038) and the strict native contract rejected all 100 of them,
    failing gmail's learning-settled behavior."""
    from packs.connector_control.contracts import validate_native_data

    validated = validate_native_data("conversation", {
        "threads": [{
            "thread_ref": "t1", "title": "hello", "message_count": 2,
            "attention_refs": ["person_email_abc123"],
        }],
        "total_count": 1,
    })
    assert validated["threads"][0]["attention_refs"] == ["person_email_abc123"]


def test_deferred_capability_stays_pending_and_delivers_identically():
    """Hardening round: a capability the host deferred is NEVER executed in
    the engine drain — the approved call stays visible for the pump, and
    deliver_capability_outcome commits the same result shape the inline
    path produces."""
    from packs.tool_gateway.gateway import (
        capability_execution_deferred,
        clear_deferred_capabilities,
        deliver_capability_outcome,
        pending_deferred_capability_calls_fn,
        perform_capability_execution,
        register_deferred_capability,
    )
    from packs.tool_gateway.settings import ToolGatewaySettings
    from packs.tool_gateway.tools import register_local_capability

    graph, runtime = _graph()
    from packs.tool_gateway import pack as gateway_pack

    runtime.load_pack(gateway_pack)
    runtime.run_until_idle()

    calls = []

    def _fetch(page_token: str = "", execution_context=None, **kwargs):
        calls.append(page_token)
        return {"messages": ["m1"], "next": None}

    from pydantic import BaseModel

    class _In(BaseModel):
        page_token: str = ""

    register_local_capability(
        "gmailish", "messages.fetch", _fetch, input_schema=_In,
        description="test fetch", risk_class="low", action_class="R0",
    )
    clear_deferred_capabilities()
    try:
        register_deferred_capability("gmailish", "messages.fetch")
        assert capability_execution_deferred("gmailish", "messages.fetch")
        call = graph.add_object("capability_call", {
            "provider_id": "", "provider_name": "gmailish",
            "capability_name": "messages.fetch",
            "input_data": {"page_token": ""},
            "credential_ref_name": None, "credential_ref_id": None,
            "risk_class": "low", "action_class": "R0",
            "status": "proposed", "proposed_by": "test",
            "frame_id": None, "proposed_at": None, "metadata": {},
        })
        runtime.run_until_idle()
        call = graph.get_object(call.id)
        assert call.data["status"] == "approved", "R0 auto-approves"
        assert calls == [], "the engine drain never executed the network fn"
        pending = pending_deferred_capability_calls_fn(graph)
        assert [row["call_id"] for row in pending] == [call.id]

        # The pump's cycle: perform off-thread, deliver on the engine.
        settings = ToolGatewaySettings()
        outcome = perform_capability_execution(
            {"provider_name": "gmailish", "capability_name": "messages.fetch",
             "input_data": {"page_token": ""}, "call_id": call.id},
            settings,
        )
        assert calls == [""], "perform ran the network half exactly once"
        delivered = deliver_capability_outcome(
            graph, call.id,
            {"provider_name": "gmailish", "capability_name": "messages.fetch"},
            settings, outcome,
        )
        runtime.run_until_idle()
        assert delivered.get("result_id")
        assert graph.get_object(call.id).data["status"] == "done"
        assert pending_deferred_capability_calls_fn(graph) == []
    finally:
        clear_deferred_capabilities()


def test_deferred_registry_is_reference_counted_per_host():
    """Two hosts in one process: one stopping must not strip deferral from
    the other; the final unregister restores inline execution."""
    from packs.tool_gateway.gateway import (
        capability_execution_deferred,
        clear_deferred_capabilities,
        register_deferred_capability,
        unregister_deferred_capability,
    )

    clear_deferred_capabilities()
    try:
        register_deferred_capability("gmail", "messages.fetch")   # host A
        register_deferred_capability("gmail", "messages.fetch")   # host B
        unregister_deferred_capability("gmail", "messages.fetch")  # A stops
        assert capability_execution_deferred("gmail", "messages.fetch"), (
            "host B's deferral survives host A's shutdown"
        )
        unregister_deferred_capability("gmail", "messages.fetch")  # B stops
        assert not capability_execution_deferred("gmail", "messages.fetch")
        # Extra unregisters are harmless (idempotent shutdown paths).
        unregister_deferred_capability("gmail", "messages.fetch")
        assert not capability_execution_deferred("gmail", "messages.fetch")
    finally:
        clear_deferred_capabilities()
