"""Run Memory Gateway Pack fixture scenarios.

The full behavior chain is now graph-driven:
  memory_candidate.created → candidate_evaluator → creates evaluation
  evaluation.created (accepted) → memory_writer → creates memory_item
  memory_retrieval_request.created → memory_retriever → creates memory_retrieval
  memory_retrieval.created → memory_ranker → creates memory_ranking

Usage:
    python packs/memory_gateway/fixtures/run_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parents[2]))

import yaml
from activegraph import Graph, Runtime
from packs.core import pack as core_pack, CoreSettings
from packs.memory_gateway import pack as mg_pack, MemoryGatewaySettings
from packs.memory_gateway.backend import clear_all_backends


def _run_fixture(name: str, scenario: dict) -> tuple[bool, list[str]]:
    failures: list[str] = []

    # Fresh backend for each fixture
    clear_all_backends()

    graph = Graph()
    rt = Runtime(graph)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(mg_pack, settings=MemoryGatewaySettings(
        acceptance_threshold=0.6,
        auto_accept_categories=["preference", "instruction", "decision"],
    ))

    # Phase 1: add all non-retrieval-request objects first
    retrieval_requests = []
    for obj_spec in scenario.get("objects", []):
        if obj_spec["type"] == "memory_retrieval_request":
            retrieval_requests.append(obj_spec)
        else:
            graph.add_object(obj_spec["type"], obj_spec["data"])

    # Let candidate → evaluation → memory_item chain complete
    rt.run_until_idle()

    # Phase 2: add retrieval requests (memory items are now in the backend)
    for obj_spec in retrieval_requests:
        graph.add_object(obj_spec["type"], obj_spec["data"])

    # Let memory_retriever → memory_retrieval → memory_ranker chain complete
    rt.run_until_idle()

    by_type: dict[str, list] = {}
    for o in graph.objects():
        by_type.setdefault(o.type, []).append(o)

    all_relations = list(graph.relations())
    relation_types = {r.type for r in all_relations}

    expected = scenario.get("expected_outputs", {})

    # --- evaluations ---
    if "evaluations" in expected:
        exp = expected["evaluations"]
        evals = by_type.get("evaluation", [])
        count = len(evals)
        if "min_count" in exp and count < exp["min_count"]:
            failures.append(f"  evaluations: expected >= {exp['min_count']}, got {count}")
        if "has_accepted" in exp:
            accepted_evals = [e for e in evals if e.data.get("judgment") == "accepted"]
            min_acc = exp["has_accepted"].get("min_count", 1)
            if len(accepted_evals) < min_acc:
                failures.append(
                    f"  evaluations: expected >= {min_acc} accepted, got {len(accepted_evals)}"
                )
        print(f"  evaluations: {count} ({sum(1 for e in evals if e.data.get('judgment')=='accepted')} accepted, "
              f"{sum(1 for e in evals if e.data.get('judgment')=='rejected')} rejected)")

    # --- memory_items ---
    if "memory_items" in expected:
        exp = expected["memory_items"]
        items = by_type.get("memory_item", [])
        count = len(items)
        if "min_count" in exp and count < exp["min_count"]:
            failures.append(f"  memory_items: expected >= {exp['min_count']}, got {count}")
        print(f"  memory_items: {count}")
        for item in items[:3]:
            print(f"    [{item.data.get('confidence', 0):.2f}] {item.data.get('text','')[:60]}")

    # --- memory_retrievals ---
    if "memory_retrievals" in expected:
        exp = expected["memory_retrievals"]
        retrievals = by_type.get("memory_retrieval", [])
        count = len(retrievals)
        if "min_count" in exp and count < exp["min_count"]:
            failures.append(f"  memory_retrievals: expected >= {exp['min_count']}, got {count}")
        print(f"  memory_retrievals: {count}")
        for r in retrievals[:2]:
            print(f"    results_count={r.data.get('results_count', 0)} query={r.data.get('query','')[:40]}")

    # --- memory_rankings ---
    if "memory_rankings" in expected:
        exp = expected["memory_rankings"]
        rankings = by_type.get("memory_ranking", [])
        count = len(rankings)
        if "min_count" in exp and count < exp["min_count"]:
            failures.append(f"  memory_rankings: expected >= {exp['min_count']}, got {count}")
        print(f"  memory_rankings: {count}")
        for rk in rankings[:3]:
            print(f"    rank={rk.data.get('rank')} score={rk.data.get('score')} item={rk.data.get('item_id','')[:20]}")

    # --- relations ---
    if "relations" in expected:
        for rel_spec in expected["relations"].get("includes", []):
            rtype = rel_spec["type"]
            if rtype not in relation_types:
                failures.append(
                    f"  relations: expected '{rtype}' ({rel_spec.get('description','')}), "
                    f"not found. Present: {sorted(relation_types)}"
                )

    return (len(failures) == 0), failures


def main():
    _HERE = Path(__file__).parent
    fixtures = sorted(_HERE.glob("*.yaml"))
    if not fixtures:
        print("No YAML fixtures found.")
        sys.exit(1)

    results = []
    for fpath in fixtures:
        scenario = yaml.safe_load(fpath.read_text())
        name = fpath.stem
        print(f"\n{'='*60}\nFixture: {name}\n{'='*60}")
        passed, failures = _run_fixture(name, scenario)
        results.append((name, passed))
        print("  PASS" if passed else f"  FAIL:")
        for f in failures:
            print(f)

    total = len(results)
    passed_count = sum(1 for _, ok in results if ok)
    print(f"\n{'='*60}\nResults: {passed_count}/{total} fixtures passed\n{'='*60}\n")
    if passed_count < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
