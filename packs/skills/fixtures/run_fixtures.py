"""Deterministic acceptance fixtures for governed skill artifacts."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

from activegraph import Graph, Runtime, TickingClock

from packs.core import pack as core_pack
from packs.skills import pack as skills_pack
from packs.skills.tools import (
    author_skill_fn,
    demote_skill_fn,
    invoke_skill_fn,
    link_skill_evaluation_fn,
    list_eligible_skills_fn,
    promote_skill_fn,
    record_promotion_evidence_fn,
)


def _runtime() -> Runtime:
    runtime = Runtime(Graph(clock=TickingClock("2026-07-09T00:00:00Z", step_seconds=1)))
    runtime.load_pack(core_pack)
    runtime.load_pack(skills_pack)
    return runtime


def run_lifecycle_fixture() -> dict:
    runtime = _runtime()
    graph = runtime.graph
    v1 = author_skill_fn(
        graph,
        "repo_audit_summary",
        "0.1.0",
        "Summarize a repository audit.",
        ["repo_audit_report.exists"],
        ["repo.read"],
        ["missing source evidence"],
        ["evidence:repo-audit"],
    )["skill"]
    v2 = author_skill_fn(
        graph,
        "repo_audit_summary",
        "0.2.0",
        "Summarize a repository audit with explicit risk ordering.",
        ["repo_audit_report.exists"],
        ["repo.read"],
        ["missing source evidence"],
        ["evidence:repo-audit-v2"],
    )["skill"]

    first = invoke_skill_fn(
        graph,
        v1.data["name"],
        v1.data["version"],
        "usage-retry",
        "trial:repo-audit:1",
        actor="fixture-agent",
        source_context={"surface_id": "fixture-surface"},
    )
    retry = invoke_skill_fn(
        graph,
        v1.data["name"],
        v1.data["version"],
        "usage-retry",
        "trial:repo-audit:1",
        actor="fixture-agent",
        source_context={"surface_id": "fixture-surface"},
    )
    assert first["created"] is True and retry["created"] is False
    assert len([event for event in graph.events if event.type == "skill.used"]) == 1

    try:
        promote_skill_fn(graph, v1.id, "missing", "sounds useful")
        raise AssertionError("promotion without recorded evidence must fail")
    except ValueError:
        pass
    proof = record_promotion_evidence_fn(
        graph,
        v1.id,
        "verification",
        ["verification:fixture-1"],
        "deterministic fixture passed",
        "fixture-owner",
    )["evidence"]
    promote_skill_fn(graph, v1.id, proof.data["evidence_id"], "fixture proof")
    exact = invoke_skill_fn(
        graph,
        v1.data["name"],
        "0.1.0",
        "usage-exact-v1",
        "run:repo-audit:2",
        execution_kind="real",
        actor="fixture-agent",
    )["usage"]
    assert exact.data["skill_version"] == "0.1.0"
    assert exact.data["skill_version_id"] == v1.id
    assert v2.id != v1.id

    evaluation = graph.add_object(
        "evaluation",
        {
            "subject_id": exact.id,
            "subject_type": "skill_usage",
            "judgment": "completed_successfully",
            "rationale": "fixture accepted",
            "evaluator": "fixture-owner",
            "metadata": {"usage_id": "usage-exact-v1"},
        },
    )
    linked = link_skill_evaluation_fn(graph, "usage-exact-v1", evaluation.id)
    assert linked["created"] is True
    assert link_skill_evaluation_fn(graph, "usage-exact-v1", evaluation.id)["created"] is False

    before_usages = len(list(graph.objects(type="skill_usage")))
    demote_skill_fn(graph, v1.id, "fixture demotion")
    assert all(item["object_id"] != v1.id for item in list_eligible_skills_fn(graph))
    assert len(list(graph.objects(type="skill_usage"))) == before_usages
    promote_skill_fn(graph, v1.id, proof.data["evidence_id"], "re-verified")
    assert any(item["object_id"] == v1.id for item in list_eligible_skills_fn(graph))
    return {
        "unique_usage_events": 2,
        "exact_version": exact.data["skill_version"],
        "reversible": True,
    }


def run_all() -> bool:
    print("=" * 60)
    print("Skills Pack Acceptance Fixtures")
    print("=" * 60)
    print("\n[1] immutable versions, idempotent usage, evidence, reversibility")
    print(f"  PASS: {run_lifecycle_fixture()}")
    print("\nALL PASS")
    return True


if __name__ == "__main__":
    try:
        ok = run_all()
    except (AssertionError, ValueError) as exc:
        print(f"\nFAIL: {exc}")
        raise SystemExit(1)
    raise SystemExit(0 if ok else 1)
