"""Closed deterministic L2 Habit loop across normalization, skills, outcomes, and memory."""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from activegraph import Graph, Runtime, TickingClock

from packs.activity_normalizer import pack as activity_normalizer_pack
from packs.core import pack as core_pack
from packs.eval_outcome import pack as eval_outcome_pack
from packs.eval_outcome.tools import get_reliability_fn, record_explicit_verdict_fn
from packs.memory_gateway import MemoryGatewaySettings, pack as memory_gateway_pack
from packs.memory_gateway.backend import clear_all_backends
from packs.memory_gateway.tools import retrieve_memories_fn
from packs.skills import pack as skills_pack
from packs.skills.tools import invoke_skill_fn
from packs.usage import pack as usage_pack
from packs.usage.tools import connect_surface_fn, record_usage_fn


def _runtime() -> Runtime:
    from packs.semantic_extraction import pack as semantic_pack

    clear_all_backends()
    runtime = Runtime(Graph(clock=TickingClock("2026-07-09T08:00:00Z", step_seconds=1)))
    runtime.load_pack(core_pack)
    runtime.load_pack(activity_normalizer_pack)
    # ADR 0026: ingestion candidates flow annotation-first — the shared
    # layer extracts, the normalizer's compat projectors mint candidates.
    runtime.load_pack(semantic_pack)
    runtime.load_pack(usage_pack)
    runtime.load_pack(skills_pack)
    runtime.load_pack(
        memory_gateway_pack,
        settings=MemoryGatewaySettings(backend_url=":memory:"),
    )
    runtime.load_pack(eval_outcome_pack)
    return runtime


def _acquire_skill_candidate(graph) -> None:
    payload = "Skill: daily_repo_digest"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    item = graph.add_object(
        "acquired_item",
        {
            "source_surface_id": "surface_l2_habit",
            "provider_item_id": "habit-skill-definition-1",
            "dedup_key": "habit-skill-definition-1",
            "source_ref": "dogfood://habit/skill-definition-1",
            "source_hash": digest,
            "provider_time": "2026-07-09T08:00:00Z",
            "replay_mode": "inline",
            "replay_payload_ref": payload,
            "replay_payload_hash": digest,
            "media_type": "text/plain",
            "importer_id": "l2_habit_fixture",
            "importer_version": "0.1.0",
        },
    )
    graph.add_object(
        "acquired_content",
        {
            "acquired_item_id": item.id,
            "normalized_content": payload,
            "normalized_metadata": {},
            "source_category": "code_work",
            "connection_path": "local",
            "is_fixture": False,
        },
    )


def _memory_reliability_scenario(runtime: Runtime) -> dict:
    graph = runtime.graph
    existing_ids = {item.id for item in graph.objects(type="memory_item")}
    for text in (
        "Habit review happens every Friday.",
        "Habit review happens every Friday with the owner.",
    ):
        graph.add_object(
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
    created = [
        item for item in graph.objects(type="memory_item") if item.id not in existing_ids
    ]
    assert len(created) == 2
    target = created[0]
    record_explicit_verdict_fn(
        graph,
        "reject",
        "the memory caused a verified wrong reminder",
        "owner:habit-fixture",
        artifact_id=target.id,
        artifact_type="memory_item",
    )
    runtime.run_until_idle()
    demoted = next(
        row
        for row in retrieve_memories_fn("habit review Friday", min_score=0.0)
        if row["item_id"] == target.id
    )
    assert demoted["score"] < demoted["raw_score"]
    assert get_reliability_fn(graph, target.id)["verdict"] == "harmful"

    record_explicit_verdict_fn(
        graph,
        "accept",
        "later evidence verified the memory",
        "owner:habit-fixture",
        artifact_id=target.id,
        artifact_type="memory_item",
    )
    runtime.run_until_idle()
    restored = next(
        row
        for row in retrieve_memories_fn("habit review Friday", min_score=0.0)
        if row["item_id"] == target.id
    )
    assert restored["score"] == restored["raw_score"]
    assert get_reliability_fn(graph, target.id)["verdict"] == "supported"
    return {
        "artifact_id": target.id,
        "demoted_multiplier": demoted["reliability_multiplier"],
        "restored_multiplier": restored["reliability_multiplier"],
    }


def run_l2_habit_fixture() -> dict:
    runtime = _runtime()
    graph = runtime.graph
    connect_surface_fn(
        graph,
        "surface_l2_habit",
        "code_work",
        provider={"name": "constitution_dogfood"},
        path="local",
        acquisition_mode="snapshot",
        is_fixture=False,
    )
    _acquire_skill_candidate(graph)
    runtime.run_until_idle()

    skill_candidates = list(graph.objects(type="skill_candidate"))
    skills = list(graph.objects(type="skill"))
    assert len(skill_candidates) == 1
    assert len(skills) == 1
    skill = skills[0]
    assert skill.data["version"] == "0.1.0"
    assert skill.data["source_candidate_id"] == skill_candidates[0].id

    first = invoke_skill_fn(
        graph,
        skill.data["name"],
        "0.1.0",
        "habit-usage-1",
        "trial:habit-loop:1",
        execution_kind="trial",
        actor="agent:habit-fixture",
        source_context={
            "source_surface_id": "surface_l2_habit",
            "source_category": "code_work",
            "provider_time": "2026-07-09T09:00:00Z",
        },
        is_fixture=False,
    )
    retry = invoke_skill_fn(
        graph,
        skill.data["name"],
        "0.1.0",
        "habit-usage-1",
        "trial:habit-loop:1",
        execution_kind="trial",
        actor="agent:habit-fixture",
        source_context={
            "source_surface_id": "surface_l2_habit",
            "source_category": "code_work",
            "provider_time": "2026-07-09T09:00:00Z",
        },
        is_fixture=False,
    )
    assert first["created"] is True and retry["created"] is False

    verdict = record_explicit_verdict_fn(
        graph,
        "accept",
        "the digest helped the owner close the review",
        "owner:habit-fixture",
        usage_id="habit-usage-1",
        source_context={
            "source_surface_id": "surface_l2_habit",
            "source_category": "outcome_evaluation",
        },
        is_fixture=False,
    )
    runtime.run_until_idle()
    assert get_reliability_fn(graph, skill.id)["verdict"] == "supported"

    record_usage_fn(
        graph,
        "habit-interaction-2026-07-09",
        "surface_l2_habit",
        "skill_interaction",
        provider_time="2026-07-09T09:00:00Z",
        is_fixture=False,
        provenance={"skill_usage_id": "habit-usage-1"},
    )
    record_usage_fn(
        graph,
        "habit-interaction-2026-07-10",
        "surface_l2_habit",
        "owner_verdict",
        provider_time="2026-07-10T09:00:00Z",
        is_fixture=False,
        provenance={"evaluation_id": verdict["evaluation_id"]},
    )
    memory = _memory_reliability_scenario(runtime)

    skill_used = [
        event
        for event in graph.events
        if event.type == "skill.used" and not event.payload.get("is_fixture", False)
    ]
    assert len(skill_used) == 1
    assert skill_used[0].payload["skill_version"] == "0.1.0"
    assert skill_used[0].payload["execution_ref"] == "trial:habit-loop:1"
    skill_terminals = [
        event
        for event in graph.events
        if event.type in {"outcome.helped", "outcome.hurt", "outcome.neutral"}
        and event.payload.get("evaluation_id") == verdict["evaluation_id"]
    ]
    assert len(skill_terminals) == 1
    interaction_dates = {
        datetime.fromisoformat(event.payload["provider_time"]).date().isoformat()
        for event in graph.events
        if event.type == "usage.recorded"
        and not event.payload.get("is_fixture", False)
        and event.payload.get("provider_time")
    }
    assert interaction_dates == {"2026-07-09", "2026-07-10"}
    return {
        "skill_version_id": skill.id,
        "skill_usage_id": "habit-usage-1",
        "skill_used_events": len(skill_used),
        "terminal_outcome": skill_terminals[0].type,
        "interaction_utc_dates": sorted(interaction_dates),
        "memory": memory,
    }


def main() -> int:
    print("=" * 60)
    print("L2 Habit Closed-Loop Fixture")
    print("=" * 60)
    result = run_l2_habit_fixture()
    print(f"  PASS: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
