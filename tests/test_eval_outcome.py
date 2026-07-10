"""P3 acceptance contract for canonical outcomes and artifact reliability."""

from __future__ import annotations

import pytest
from activegraph import Graph, Runtime, TickingClock

from packs.core import pack as core_pack
from packs.eval_outcome import pack as eval_outcome_pack
from packs.eval_outcome.object_types import ArtifactReliability
from packs.eval_outcome.tools import (
    get_reliability_fn,
    record_explicit_verdict_fn,
    record_maintenance_outcome_fn,
    record_terminal_outcome_fn,
    supersede_evaluation_fn,
)
from packs.memory_gateway import MemoryGatewaySettings, pack as memory_gateway_pack
from packs.memory_gateway.backend import clear_all_backends
from packs.memory_gateway.tools import retrieve_memories_fn
from packs.skills import pack as skills_pack
from packs.skills.tools import (
    author_skill_fn,
    invoke_skill_fn,
    promote_skill_fn,
    record_promotion_evidence_fn,
)
from packs.tool_gateway import pack as tool_gateway_pack


def _runtime(*packs) -> Runtime:
    runtime = Runtime(Graph(clock=TickingClock("2026-07-09T00:00:00Z", step_seconds=1)))
    runtime.load_pack(core_pack)
    for pack in packs:
        runtime.load_pack(pack)
    return runtime


def _artifact_and_evaluation(graph):
    artifact = graph.add_object(
        "artifact",
        {
            "kind": "report",
            "title": "Acceptance artifact",
            "content": "deterministic",
            "status": "published",
        },
    )
    evaluation = graph.add_object(
        "evaluation",
        {
            "subject_id": artifact.id,
            "subject_type": "artifact",
            "judgment": "accepted",
            "rationale": "owner accepted",
            "evaluator": "owner:test",
        },
    )
    return artifact, evaluation


def test_terminal_outcomes_are_mutually_exclusive_per_evaluation() -> None:
    runtime = _runtime(eval_outcome_pack)
    graph = runtime.graph
    artifact, evaluation = _artifact_and_evaluation(graph)
    first = record_terminal_outcome_fn(
        graph,
        "outcome.helped",
        evaluation.id,
        "accepted",
        "owner:test",
        artifact_id=artifact.id,
        artifact_type="artifact",
    )
    assert first["created"] is True
    assert record_terminal_outcome_fn(
        graph,
        "outcome.helped",
        evaluation.id,
        "retry",
        "owner:test",
        artifact_id=artifact.id,
        artifact_type="artifact",
    )["created"] is False
    with pytest.raises(ValueError, match="correction requires supersession"):
        record_terminal_outcome_fn(
            graph,
            "outcome.hurt",
            evaluation.id,
            "changed mind",
            "owner:test",
            artifact_id=artifact.id,
            artifact_type="artifact",
        )
    terminals = [event for event in graph.events if event.type in {
        "outcome.helped", "outcome.hurt", "outcome.neutral"
    }]
    assert len(terminals) == 1


def test_correction_uses_supersession_and_never_duplicates_old_terminal() -> None:
    runtime = _runtime(eval_outcome_pack)
    graph = runtime.graph
    artifact, evaluation = _artifact_and_evaluation(graph)
    record_terminal_outcome_fn(
        graph,
        "outcome.hurt",
        evaluation.id,
        "initial rejection",
        "owner:test",
        artifact_id=artifact.id,
        artifact_type="artifact",
    )
    correction = supersede_evaluation_fn(
        graph,
        evaluation.id,
        "accept",
        "corrected after review",
        "owner:test",
    )
    runtime.run_until_idle()
    old_terminals = [
        record
        for record in graph.objects(type="outcome_record")
        if record.data.get("evaluation_id") == evaluation.id
        and record.data.get("outcome_type") in {"helped", "hurt", "neutral"}
    ]
    assert len(old_terminals) == 1
    assert correction["supersession"].data["outcome_type"] == "superseded"
    assert correction["supersession"].data["superseding_version"] == (
        correction["replacement_evaluation"].id
    )
    assert correction["terminal"].data["outcome_type"] == "helped"


def test_negative_valence_is_queryable_reliability_not_player_state() -> None:
    runtime = _runtime(eval_outcome_pack)
    graph = runtime.graph
    artifact, evaluation = _artifact_and_evaluation(graph)
    record_terminal_outcome_fn(
        graph,
        "outcome.hurt",
        evaluation.id,
        "verified failure",
        "owner:test",
        artifact_id=artifact.id,
        artifact_type="tool_policy",
        artifact_version="1",
    )
    runtime.run_until_idle()
    reliability = get_reliability_fn(graph, artifact.id)
    assert reliability["verdict"] == "harmful"
    assert reliability["eligible"] is False
    assert reliability["tallies"]["hurt"] == 1
    forbidden = {"score", "points", "badge", "level", "player"}
    assert forbidden.isdisjoint(ArtifactReliability.model_fields)
    assert forbidden.isdisjoint(reliability)


def _memory_runtime() -> Runtime:
    clear_all_backends()
    runtime = Runtime(Graph(clock=TickingClock("2026-07-09T00:00:00Z", step_seconds=1)))
    runtime.load_pack(core_pack)
    runtime.load_pack(
        memory_gateway_pack,
        settings=MemoryGatewaySettings(backend_url=":memory:", acceptance_threshold=0.6),
    )
    runtime.load_pack(eval_outcome_pack)
    return runtime


def _seed_memories(runtime: Runtime):
    for text in (
        "Project alpha deadline Tuesday.",
        "Project alpha deadline Tuesday confirmed by owner.",
    ):
        runtime.graph.add_object(
            "memory_candidate",
            {
                "text": text,
                "confidence": 0.95,
                "source_ids": [],
                "observation_ids": [],
                "category": "fact",
                "accepted": False,
            },
        )
    runtime.run_until_idle()
    return list(runtime.graph.objects(type="memory_item"))


def test_memory_demotes_in_retrieval_and_later_helped_restores_it() -> None:
    runtime = _memory_runtime()
    graph = runtime.graph
    items = _seed_memories(runtime)
    assert len(items) == 2
    target = items[0]
    record_explicit_verdict_fn(
        graph,
        "reject",
        "memory caused a wrong action",
        "owner:test",
        artifact_id=target.id,
        artifact_type="memory_item",
    )
    runtime.run_until_idle()
    harmful = get_reliability_fn(graph, target.id)
    assert harmful["verdict"] == "harmful"
    ranked = retrieve_memories_fn("project alpha deadline Tuesday", min_score=0.0)
    target_row = next(row for row in ranked if row["item_id"] == target.id)
    peer_row = next(row for row in ranked if row["item_id"] != target.id)
    assert target_row["score"] == pytest.approx(target_row["raw_score"] * 0.1)
    assert target_row["score"] < peer_row["score"]
    patched = graph.get_object(target.id)
    assert patched.data["reliability_verdict"] == "harmful"

    record_explicit_verdict_fn(
        graph,
        "accept",
        "later verified as useful",
        "owner:test",
        artifact_id=target.id,
        artifact_type="memory_item",
    )
    runtime.run_until_idle()
    supported = get_reliability_fn(graph, target.id)
    assert supported["verdict"] == "supported"
    restored = retrieve_memories_fn("project alpha deadline Tuesday", min_score=0.0)
    restored_row = next(row for row in restored if row["item_id"] == target.id)
    assert restored_row["score"] == restored_row["raw_score"]
    assert graph.get_object(target.id).data["reliability_multiplier"] == 1.0


def test_every_outcome_has_subject_trace_and_maintenance_composite_key() -> None:
    runtime = _memory_runtime()
    graph = runtime.graph
    item = _seed_memories(runtime)[0]
    terminal = record_explicit_verdict_fn(
        graph,
        "neutral",
        "no material effect",
        "owner:test",
        artifact_id=item.id,
        artifact_type="memory_item",
    )["record"]
    maintenance = record_maintenance_outcome_fn(
        graph,
        "outcome.contradicted",
        item.id,
        "memory_item",
        "new evidence conflicts",
        "memory_layer",
        evidence_revision_id="revision:newer-1",
    )["record"]
    for record in (terminal, maintenance):
        assert record.data["artifact_id"] == item.id
        assert record.data["artifact_type"] == "memory_item"
        assert record.data["outcome_event_id"]
        assert record.data["contribution_key"]
    assert terminal.data["evaluation_id"]
    assert maintenance.data["evidence_revision_id"] == "revision:newer-1"
    retry = record_maintenance_outcome_fn(
        graph,
        "outcome.contradicted",
        item.id,
        "memory_item",
        "retry",
        "memory_layer",
        evidence_revision_id="revision:newer-1",
    )
    assert retry["created"] is False


def test_task_and_memory_contradiction_evaluations_capture_without_ui() -> None:
    runtime = _memory_runtime()
    graph = runtime.graph
    item = _seed_memories(runtime)[0]
    task = graph.add_object(
        "task",
        {"title": "Close loop", "status": "done", "priority": "medium"},
    )
    graph.add_object(
        "evaluation",
        {
            "subject_id": task.id,
            "subject_type": "task",
            "judgment": "completed_successfully",
            "rationale": "task completed",
            "evaluator": "task_layer",
        },
    )
    graph.add_object(
        "evaluation",
        {
            "subject_id": item.id,
            "subject_type": "memory_item",
            "judgment": "contradicted",
            "rationale": "new source disagrees",
            "evaluator": "memory_layer",
            "metadata": {"evidence_revision_id": "revision:conflict-1"},
        },
    )
    runtime.run_until_idle()
    assert any(event.type == "outcome.helped" for event in graph.events)
    assert any(event.type == "outcome.contradicted" for event in graph.events)
    assert get_reliability_fn(graph, item.id)["verdict"] == "harmful"


def test_skill_eligibility_uses_the_same_reversible_reliability_handoff() -> None:
    runtime = _runtime(skills_pack, eval_outcome_pack)
    graph = runtime.graph
    skill = author_skill_fn(
        graph,
        "daily_digest",
        "0.1.0",
        "Prepare a bounded daily digest.",
        source_evidence_refs=["evidence:digest"],
    )["skill"]
    proof = record_promotion_evidence_fn(
        graph,
        skill.id,
        "verification",
        ["verification:digest"],
        "fixture verified",
        "owner:test",
    )["evidence"]
    promote_skill_fn(graph, skill.id, proof.data["evidence_id"], "verified")
    invoke_skill_fn(
        graph,
        "daily_digest",
        "0.1.0",
        "usage-digest",
        "run:digest",
        execution_kind="real",
        actor="agent:test",
    )
    hurt = record_explicit_verdict_fn(
        graph,
        "reject",
        "digest omitted a critical item",
        "owner:test",
        usage_id="usage-digest",
    )
    runtime.run_until_idle()
    demoted = graph.get_object(skill.id)
    assert demoted.data["status"] == "demoted"
    demotion = [event for event in graph.events if event.type == "skill.demoted"][-1]
    assert demotion.payload["outcome_evidence_event_id"] == (
        hurt["record"].data["outcome_event_id"]
    )

    record_explicit_verdict_fn(
        graph,
        "accept",
        "later run verified the exact version",
        "owner:test",
        artifact_id=skill.id,
        artifact_type="skill_version",
        artifact_version="0.1.0",
    )
    runtime.run_until_idle()
    restored = graph.get_object(skill.id)
    assert restored.data["status"] == "promoted"
    assert get_reliability_fn(graph, skill.id)["verdict"] == "supported"


def test_gateway_result_acceptance_evaluation_captures_helped() -> None:
    runtime = _runtime(tool_gateway_pack, eval_outcome_pack)
    graph = runtime.graph
    result = graph.add_object(
        "capability_result",
        {
            "call_id": "capability_call#fixture",
            "provider_name": "fixture",
            "capability_name": "lookup",
            "output_data": "accepted result",
            "success": True,
        },
    )
    evaluation = graph.add_object(
        "evaluation",
        {
            "subject_id": result.id,
            "subject_type": "capability_result",
            "judgment": "accepted",
            "rationale": "owner accepted the executed result",
            "evaluator": "owner:test",
        },
    )
    runtime.run_until_idle()
    terminal = [
        record
        for record in graph.objects(type="outcome_record")
        if record.data.get("evaluation_id") == evaluation.id
    ]
    assert len(terminal) == 1
    assert terminal[0].data["outcome_type"] == "helped"
    assert terminal[0].data["artifact_id"] == result.id
