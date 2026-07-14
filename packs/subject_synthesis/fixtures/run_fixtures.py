"""Governed-coordination conformance (ADR 0047), zero-key by construction.

One deterministic campaign end to end: a fixture affordance registers,
lenses contribute with support-vs-context lineage, borrowed context fails
to corroborate, the working understanding versions, the deterministic
proposer synthesizes then stops, and the move ledger records every
validation verdict. No LLM, no API key, no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parents[2]))

from activegraph import Graph, Runtime

from packs.subject_profile import pack as subject_profile_pack
from packs.subject_synthesis import pack
from packs.subject_synthesis.affordance import (
    register_understanding_affordance,
    unregister_understanding_affordance,
)
from packs.subject_synthesis.coordinator import (
    current_campaign_fn,
    open_comprehension_campaign_fn,
    project_comprehension_campaign_fn,
    propose_next_move_deterministic_fn,
    record_coordinator_move_fn,
    settle_campaign_fn,
    settle_coordinator_move_fn,
    validate_coordinator_move_fn,
)
from packs.subject_synthesis.working import (
    compose_working_understanding_fn,
    contribute_source_lens_fn,
    project_working_understanding_fn,
    settle_source_lens_fn,
)

FIXTURE_AFFORDANCE_ID = "fixture_notes_understanding"


def _fixture_affordance() -> dict:
    return {
        "affordance_id": FIXTURE_AFFORDANCE_ID,
        "version": "0.1.0",
        "service": "fixture_notes",
        "family": "records",
        "teaches": ["topics"],
        "capabilities": [
            {"capability": "notes.list", "action_class": "R1",
             "scopes": ["exported_notes"]},
        ],
        "schemas": {
            "input": {"max_items": "int"},
            "output": {"leaf_schema": ["topics"]},
            "evidence_ref": "note evidence id",
        },
        "privacy": {"excludes": [], "outward_disclosure": "provider_only"},
        "reductions": {"leaf_schema": ["topics"]},
        "drill_down": {"allowed": False},
        "bounds": {"max_items": 20, "max_seconds": 60, "max_tokens": 1_000,
                   "max_cost_milli": 100},
        "moves": ["inspect_source", "reduce_fast"],
        "destinations": ["subject_profile"],
        "coverage_required": True,
    }


def run_fixture() -> dict:
    runtime = Runtime(Graph())
    runtime.load_pack(subject_profile_pack)
    runtime.load_pack(pack)
    runtime.run_until_idle()
    graph = runtime.graph

    register_understanding_affordance(_fixture_affordance())
    try:
        opened = open_comprehension_campaign_fn(
            graph, selected_affordances=[FIXTURE_AFFORDANCE_ID],
        )
        assert opened["created"], "the campaign opens"
        campaign = current_campaign_fn(graph)

        # An owner fact plus one supported lens contribution.
        graph.add_object("subject_fact", {
            "fact_identity": "fact:name", "subject_ref": "owner",
            "attribute": "name", "value": "Fixture Owner",
            "text": "name: Fixture Owner", "status": "promoted",
            "verdict_id": "verdict:seed",
        })
        contribute_source_lens_fn(
            graph, affordance_id=FIXTURE_AFFORDANCE_ID,
            source_surface_id="fixture_notes:owner",
            contributions=[
                {"kind": "hypothesis", "statement": "studies graph runtimes",
                 "support_refs": ["note:1"]},
                {"kind": "hypothesis", "statement": "unsupported echo",
                 "support_refs": [], "context_refs": ["elsewhere:1"]},
            ],
        )
        composed = compose_working_understanding_fn(graph)
        assert composed["created"] and composed["version"] == 1
        packet = project_working_understanding_fn(graph)
        supported = next(r for r in packet["entries"]
                         if r["statement"] == "studies graph runtimes")
        assert supported["corroboration"] == 1
        assert supported["authority"] == "hypothesis"
        echoes = [r for r in packet["unresolved"]
                  if r["kind"] == "uncorroborated_echo"]
        assert not echoes, "unsupported rows were dropped at the lens"

        # In-flight lens: the deterministic proposer refuses busy moves.
        assert propose_next_move_deterministic_fn(graph, campaign) is None

        settle_source_lens_fn(
            graph, affordance_id=FIXTURE_AFFORDANCE_ID,
            source_surface_id="fixture_notes:owner", terminal="contributed",
        )
        move = propose_next_move_deterministic_fn(graph, campaign)
        assert move["kind"] == "synthesize"
        verdict = validate_coordinator_move_fn(graph, campaign, move)
        assert verdict["verdict"] == "execute"
        recorded = record_coordinator_move_fn(graph, campaign.id, move)
        assert recorded["verdict"] == "execute"
        settle_coordinator_move_fn(
            graph, recorded["move_id"], status="committed",
            result={"summary": "draft requested"},
        )

        graph.add_object("setup_draft", {
            "draft_identity": "draft:v1", "version": 1, "subject_ref": "owner",
            "status": "submitted", "source": "deterministic", "run_id": None,
            "supersedes": None, "included_refs": [], "coverage": {},
            "counts": {}, "metadata": {},
        })
        stop = propose_next_move_deterministic_fn(graph, campaign)
        assert stop["kind"] == "stop"
        recorded_stop = record_coordinator_move_fn(graph, campaign.id, stop)
        settle_coordinator_move_fn(graph, recorded_stop["move_id"], status="committed")
        settle_campaign_fn(graph, campaign.id, status="completed",
                           stop_reason="review_ready")

        projected = project_comprehension_campaign_fn(graph)
        assert projected["status"] == "completed"
        assert projected["stop_reason"] == "review_ready"
        assert [m["kind"] for m in projected["moves"]] == ["synthesize", "stop"]
        assert all(m["validation"]["verdict"] == "execute"
                   for m in projected["moves"])
        return {
            "moves": len(projected["moves"]),
            "working_version": packet["version"],
            "corroboration": supported["corroboration"],
        }
    finally:
        unregister_understanding_affordance(FIXTURE_AFFORDANCE_ID)


if __name__ == "__main__":
    try:
        print(f"Governed Coordination Fixtures PASS: {run_fixture()}")
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)
