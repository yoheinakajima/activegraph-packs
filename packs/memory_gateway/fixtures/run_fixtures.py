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


class _CountingProvider:
    """Runtime EmbeddingProvider double: deterministic vectors, call count."""

    default_model = "fixture-embed-1"

    def __init__(self):
        self.calls = 0

    def embed(self, *, texts, model):
        self.calls += 1
        return [[float(len(t)), float(sum(map(ord, t)) % 97), 1.0]
                for t in texts]


class _PoisonProvider:
    """Raises on any embed call — proves zero external contact on replay."""

    default_model = "fixture-embed-1"

    def embed(self, *, texts, model):
        raise AssertionError("external embedding contact during replay")


def _run_recorded_embedding_fixture(tmp_dir: str) -> tuple[bool, list[str]]:
    """P10 acceptance: a memory-gateway embedding round-trip — write-time
    item embedding plus query embedding through ctx.embed — is recorded as
    embedding.requested/responded events and REPLAYS from the log with
    zero external contact (the replay runtime's provider raises if ever
    called; the recorded cache serves the same retrieval)."""
    import os

    failures: list[str] = []
    clear_all_backends()
    db = os.path.join(tmp_dir, "p10_replay.db")
    text = "the user prefers dark mode everywhere"
    settings = MemoryGatewaySettings(
        acceptance_threshold=0.6,
        auto_accept_categories=["preference"],
    )

    live_provider = _CountingProvider()
    live = Runtime(Graph(), persist_to=db, embedding_provider=live_provider)
    live.load_pack(core_pack, settings=CoreSettings())
    live.load_pack(mg_pack, settings=settings)
    live.graph.add_object("memory_candidate", {
        "text": text, "confidence": 0.85, "source_ids": [],
        "observation_ids": [], "category": "preference",
        "subject_ref": None, "accepted": False, "evaluation_id": None,
        "frame_id": "frame_p10",
    })
    live.run_until_idle()
    req = live.graph.add_object("memory_retrieval_request", {
        "query": text, "top_k": 5, "min_score": 0.0,
        "behavior_name": "p10_fixture",
    })
    live.run_until_idle()
    retrievals = [o for o in live.graph.objects(type="memory_retrieval")
                  if o.data.get("request_id") == req.id]
    live_items = list(retrievals[-1].data.get("item_ids") or []) if retrievals else []
    if not live_items:
        failures.append("  live run: stored memory was not recalled")
    if live_provider.calls != 2:
        failures.append(f"  live run: expected 2 recorded embeds (write+query), got {live_provider.calls}")
    pairs = [e for e in live.graph.events if e.type in ("embedding.requested", "embedding.responded")]
    if len(pairs) != 4:
        failures.append(f"  live run: expected 2 embedding event pairs, got {len(pairs)} events")

    replay = Runtime.load(db, embedding_provider=_PoisonProvider(),
                          replay_embedding_cache=True)
    replay.load_pack(core_pack, settings=CoreSettings())
    replay.load_pack(mg_pack, settings=settings)
    req = replay.graph.add_object("memory_retrieval_request", {
        "query": text, "top_k": 5, "min_score": 0.0,
        "behavior_name": "p10_fixture",
    })
    try:
        replay.run_until_idle()
    except AssertionError as exc:
        failures.append(f"  replay contacted the provider: {exc}")
        return (False, failures)
    retrievals = [o for o in replay.graph.objects(type="memory_retrieval")
                  if o.data.get("request_id") == req.id]
    replay_items = list(retrievals[-1].data.get("item_ids") or []) if retrievals else []
    if replay_items != live_items:
        failures.append(f"  replay retrieval {replay_items} != live {live_items}")
    hits = [e for e in replay.graph.events
            if e.type == "embedding.requested" and e.payload.get("cache_hit") is True]
    if not hits:
        failures.append("  replay retrieval did not hit the recorded embedding cache")

    print("  recorded write+query embeds, then replayed the same retrieval")
    print("  against a raise-on-contact provider: served from the log")
    return (len(failures) == 0), failures


def _run_promotion_fixture(tmp_dir: str) -> tuple[bool, list[str]]:
    """P6: reliability generates a promotion proposal; explicit approval
    promotes with the contract key; the promoted version earns
    replay.verified; a hurt outcome generates a demotion proposal whose
    approval demotes — every transition explainable to evidence."""
    import os

    from packs.eval_outcome import pack as eval_outcome_pack
    from packs.eval_outcome.tools import record_terminal_outcome_fn
    from packs.memory_gateway.promotion import (
        resolve_memory_promotion_fn,
        verify_memory_replay_fn,
    )

    failures: list[str] = []
    clear_all_backends()
    rt = Runtime(Graph(), persist_to=os.path.join(tmp_dir, "p6_memory.db"))
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(eval_outcome_pack)
    rt.load_pack(mg_pack, settings=MemoryGatewaySettings(
        acceptance_threshold=0.6, auto_accept_categories=["preference"],
    ))

    rt.graph.add_object("memory_candidate", {
        "text": "the user prefers dark mode everywhere", "confidence": 0.85,
        "source_ids": [], "observation_ids": [], "category": "preference",
        "subject_ref": None, "accepted": False, "evaluation_id": None,
        "frame_id": "frame_p6",
    })
    rt.run_until_idle()
    item = list(rt.graph.objects(type="memory_item"))[-1]

    def record(kind, rationale):
        ev = rt.graph.add_object("evaluation", {
            "subject_id": item.id, "subject_type": "memory_item",
            "judgment": "accepted", "rationale": rationale,
            "evaluator": "owner:fixture",
        })
        record_terminal_outcome_fn(
            rt.graph, kind, evaluation_id=ev.id, rationale=rationale,
            actor="owner", artifact_id=item.id, artifact_type="memory_item",
            artifact_version="1",
        )
        rt.run_until_idle()

    record("outcome.helped", "helped once")
    record("outcome.helped", "helped twice")
    proposals = [o for o in rt.graph.objects(type="memory_promotion_proposal")
                 if o.data.get("status") == "proposed"]
    if len(proposals) != 1 or proposals[0].data["direction"] != "promote":
        failures.append(f"  expected one open promote proposal, got {len(proposals)}")
        return (False, failures)
    if [e for e in rt.graph.events if e.type == "memory.promoted"]:
        failures.append("  nothing may promote before approval")

    resolve_memory_promotion_fn(rt.graph, proposals[0].data["proposal_id"],
                                approve=True, approver="user:owner")
    promoted = [e for e in rt.graph.events if e.type == "memory.promoted"]
    if len(promoted) != 1 or promoted[0].payload.get("artifact_version") != "1":
        failures.append("  memory.promoted must carry (artifact_id, artifact_version)")

    out = verify_memory_replay_fn(rt.graph, item.id, runtime=rt)
    verified = [e for e in rt.graph.events if e.type == "replay.verified"]
    if not out.get("created") or len(verified) != 1:
        failures.append("  promoted version must earn replay.verified once")
    elif verified[0].payload.get("subject_id") != item.id:
        failures.append("  replay.verified must be keyed by subject_id")

    record("outcome.hurt", "it misled a reply")
    demotes = [o for o in rt.graph.objects(type="memory_promotion_proposal")
               if o.data.get("status") == "proposed"
               and o.data.get("direction") == "demote"]
    if len(demotes) != 1:
        failures.append("  harmful reliability must generate a demotion proposal")
    else:
        resolve_memory_promotion_fn(rt.graph, demotes[0].data["proposal_id"],
                                    approve=True, approver="user:owner")
        if rt.graph.get_object(item.id).data["promotion_status"] != "demoted":
            failures.append("  approved demotion must demote the version")

    print("  proposal -> approval -> memory.promoted -> replay.verified ->")
    print("  hurt -> demotion proposal -> approved demotion: all evidenced")
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

    import tempfile
    name = "memory_promotion_loop"
    print(f"\n{'='*60}\nFixture: {name}\n{'='*60}")
    with tempfile.TemporaryDirectory() as tmp_dir:
        passed, failures = _run_promotion_fixture(tmp_dir)
    results.append((name, passed))
    print("  PASS" if passed else f"  FAIL:")
    for f in failures:
        print(f)

    name = "recorded_embedding_replay"
    print(f"\n{'='*60}\nFixture: {name}\n{'='*60}")
    with tempfile.TemporaryDirectory() as tmp_dir:
        passed, failures = _run_recorded_embedding_fixture(tmp_dir)
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
