"""Deterministic fixtures for outcome exclusivity and reversible reliability."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

from activegraph import Graph, Runtime, TickingClock

from packs.core import pack as core_pack
from packs.eval_outcome import pack as eval_outcome_pack
from packs.eval_outcome.tools import get_reliability_fn, record_explicit_verdict_fn
from packs.memory_gateway import MemoryGatewaySettings, pack as memory_gateway_pack
from packs.memory_gateway.backend import clear_all_backends
from packs.memory_gateway.tools import retrieve_memories_fn


def run_reliability_fixture() -> dict:
    clear_all_backends()
    runtime = Runtime(Graph(clock=TickingClock("2026-07-09T00:00:00Z", step_seconds=1)))
    runtime.load_pack(core_pack)
    runtime.load_pack(
        memory_gateway_pack,
        settings=MemoryGatewaySettings(backend_url=":memory:"),
    )
    runtime.load_pack(eval_outcome_pack)
    graph = runtime.graph
    for text in ("Project alpha deadline Tuesday.", "Project alpha deadline Tuesday confirmed."):
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
    target = list(graph.objects(type="memory_item"))[0]

    rejected = record_explicit_verdict_fn(
        graph,
        "reject",
        "verified harmful",
        "fixture-owner",
        artifact_id=target.id,
        artifact_type="memory_item",
    )
    runtime.run_until_idle()
    assert get_reliability_fn(graph, target.id)["verdict"] == "harmful"
    demoted = next(
        row
        for row in retrieve_memories_fn("project alpha deadline Tuesday", min_score=0.0)
        if row["item_id"] == target.id
    )
    assert demoted["score"] < demoted["raw_score"]

    record_explicit_verdict_fn(
        graph,
        "accept",
        "later verified helpful",
        "fixture-owner",
        artifact_id=target.id,
        artifact_type="memory_item",
    )
    runtime.run_until_idle()
    restored = next(
        row
        for row in retrieve_memories_fn("project alpha deadline Tuesday", min_score=0.0)
        if row["item_id"] == target.id
    )
    assert restored["score"] == restored["raw_score"]
    return {
        "terminal_evaluation": rejected["evaluation_id"],
        "harmful_multiplier": demoted["reliability_multiplier"],
        "restored_multiplier": restored["reliability_multiplier"],
    }


def run_all() -> bool:
    print("=" * 60)
    print("Eval Outcome Pack Acceptance Fixtures")
    print("=" * 60)
    print("\n[1] terminal outcome → harmful de-rank → helped reversal")
    print(f"  PASS: {run_reliability_fixture()}")
    print("\nALL PASS")
    return True


if __name__ == "__main__":
    try:
        ok = run_all()
    except (AssertionError, ValueError) as exc:
        print(f"\nFAIL: {exc}")
        raise SystemExit(1)
    raise SystemExit(0 if ok else 1)
