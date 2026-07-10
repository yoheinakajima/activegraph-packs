"""P4 acceptance contract for governed learned skill artifacts."""

from __future__ import annotations

import pytest
from activegraph import Graph, Runtime, TickingClock

from packs.activity_normalizer import pack as activity_normalizer_pack
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


def _author(graph, version: str = "0.1.0"):
    return author_skill_fn(
        graph,
        "repo_audit_summary",
        version,
        f"Repository audit summary definition {version}.",
        ["repo_audit_report.exists"],
        ["repo.read"],
        ["missing source evidence"],
        [f"evidence:{version}"],
    )["skill"]


def _proof(graph, skill):
    return record_promotion_evidence_fn(
        graph,
        skill.id,
        "verification",
        [f"verification:{skill.data['version']}"],
        "acceptance fixture passed",
        "owner:test",
    )["evidence"]


def test_usage_id_executes_and_evaluates_at_most_once_under_retries() -> None:
    graph = _runtime().graph
    skill = _author(graph)
    first = invoke_skill_fn(
        graph,
        skill.data["name"],
        skill.data["version"],
        "usage-1",
        "trial:1",
        actor="agent:test",
    )
    retry = invoke_skill_fn(
        graph,
        skill.data["name"],
        skill.data["version"],
        "usage-1",
        "trial:1",
        actor="agent:test",
    )
    assert first["created"] is True
    assert retry["created"] is False
    assert len(list(graph.objects(type="skill_usage"))) == 1
    assert len([event for event in graph.events if event.type == "skill.used"]) == 1

    evaluation = graph.add_object(
        "evaluation",
        {
            "subject_id": first["usage"].id,
            "subject_type": "skill_usage",
            "judgment": "completed_successfully",
            "metadata": {"usage_id": "usage-1"},
        },
    )
    assert link_skill_evaluation_fn(graph, "usage-1", evaluation.id)["created"] is True
    assert link_skill_evaluation_fn(graph, "usage-1", evaluation.id)["created"] is False
    second = graph.add_object(
        "evaluation",
        {
            "subject_id": first["usage"].id,
            "subject_type": "skill_usage",
            "judgment": "needs_revision",
            "metadata": {"usage_id": "usage-1"},
        },
    )
    with pytest.raises(ValueError, match="already been evaluated"):
        link_skill_evaluation_fn(graph, "usage-1", second.id)
    assert len([event for event in graph.events if event.type == "skill.evaluated"]) == 1


def test_promotion_requires_recorded_evidence_and_is_explainable() -> None:
    graph = _runtime().graph
    skill = _author(graph)
    with pytest.raises(ValueError, match="recorded evidence"):
        promote_skill_fn(graph, skill.id, "missing", "sounds useful")
    proof = _proof(graph, skill)
    result = promote_skill_fn(
        graph,
        skill.id,
        proof.data["evidence_id"],
        "verified acceptance behavior",
        "owner:test",
    )
    promoted = result["skill"]
    assert promoted.data["status"] == "promoted"
    history = promoted.data["promotion_history"]
    assert history[-1]["evidence_id"] == proof.data["evidence_id"]
    assert history[-1]["rationale"] == "verified acceptance behavior"
    event = [event for event in graph.events if event.type == "skill.promoted"][-1]
    assert event.payload["skill_version"] == "0.1.0"


def test_demotion_removes_eligibility_preserves_history_and_is_reversible() -> None:
    graph = _runtime().graph
    skill = _author(graph)
    proof = _proof(graph, skill)
    promote_skill_fn(graph, skill.id, proof.data["evidence_id"], "verified")
    invoke_skill_fn(
        graph,
        skill.data["name"],
        skill.data["version"],
        "usage-history",
        "run:history",
        execution_kind="real",
        actor="agent:test",
    )
    assert any(row["object_id"] == skill.id for row in list_eligible_skills_fn(graph))
    demote_skill_fn(graph, skill.id, "harmful verified outcome")
    assert all(row["object_id"] != skill.id for row in list_eligible_skills_fn(graph))
    with pytest.raises(ValueError, match="not eligible"):
        invoke_skill_fn(
            graph,
            skill.data["name"],
            skill.data["version"],
            "usage-blocked",
            "run:blocked",
        )
    assert len(list(graph.objects(type="skill_usage"))) == 1
    restored = promote_skill_fn(graph, skill.id, proof.data["evidence_id"], "re-verified")
    assert restored["skill"].data["status"] == "promoted"
    assert len(restored["skill"].data["promotion_history"]) == 2


def test_version_selection_is_exact_and_material_changes_require_new_version() -> None:
    graph = _runtime().graph
    v1 = _author(graph, "0.1.0")
    v2 = _author(graph, "0.2.0")
    proof = _proof(graph, v1)
    promote_skill_fn(graph, v1.id, proof.data["evidence_id"], "v1 verified")
    usage = invoke_skill_fn(
        graph,
        v1.data["name"],
        "0.1.0",
        "usage-v1-after-v2",
        "run:v1",
        execution_kind="real",
        actor="agent:test",
    )["usage"]
    assert usage.data["skill_version_id"] == v1.id
    assert usage.data["skill_version"] == "0.1.0"
    assert usage.data["skill_version_id"] != v2.id

    with pytest.raises(ValueError, match="materially new semantic version"):
        author_skill_fn(
            graph,
            v1.data["name"],
            "0.1.0",
            "Changed definition under the same version.",
            source_evidence_refs=["evidence:changed"],
        )


def test_normalizer_skill_candidate_becomes_a_provenance_backed_proposal() -> None:
    runtime = Runtime(Graph(clock=TickingClock("2026-07-09T00:00:00Z", step_seconds=1)))
    runtime.load_pack(core_pack)
    runtime.load_pack(activity_normalizer_pack)
    runtime.load_pack(skills_pack)
    candidate = runtime.graph.add_object(
        "skill_candidate",
        {
            "candidate_identity": "candidate-skill-1",
            "text": "Summarize repo audits.",
            "confidence": 0.8,
            "evidence_id": "activity_evidence#1",
            "evidence_identity": "evidence-1",
            "revision_id": "revision-1",
            "extraction_record_id": "extraction_record#1",
            "extractor_id": "activity.structure",
            "extractor_version": "0.1.0",
            "extraction_config_id": "default",
            "status": "candidate",
            "name": "repo_audit_summary",
            "description": "Summarize repo audits.",
            "metadata": {},
        },
    )
    runtime.run_until_idle()
    skills = list(runtime.graph.objects(type="skill"))
    assert len(skills) == 1
    assert skills[0].data["source_candidate_id"] == candidate.id
    assert "activity_evidence#1" in skills[0].data["source_evidence_refs"]
    proposed = [event for event in runtime.graph.events if event.type == "skill.proposed"]
    assert len(proposed) == 1
    assert proposed[0].payload["source_candidate_id"] == candidate.id
