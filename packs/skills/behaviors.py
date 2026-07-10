"""Graph-composed behaviors for skill proposals, evaluations, and eligibility."""

from __future__ import annotations

from activegraph.packs import behavior

from .settings import SkillsSettings
from .tools import _emit_event, author_skill_fn, link_skill_evaluation_fn


@behavior(
    name="skill_candidate_author",
    on=["object.created"],
    where={"object.type": "skill_candidate"},
    view={"include_types": ["skill"]},
    creates=["skill"],
)
def skill_candidate_author(event, graph, ctx, *, settings: SkillsSettings):
    """Turn a normalizer skill candidate into a provenance-backed v0 artifact."""

    if not settings.enabled:
        return
    wrapper = event.payload.get("object", {})
    candidate_id = wrapper.get("id")
    data = wrapper.get("data", {})
    if not candidate_id or data.get("status") != "candidate":
        return
    metadata = dict(data.get("metadata") or {})
    refs = [
        str(value)
        for value in (
            candidate_id,
            data.get("evidence_id"),
            data.get("revision_id"),
            data.get("extraction_record_id"),
        )
        if value
    ]
    author_skill_fn(
        graph,
        name=str(data.get("name") or data.get("text") or "")[:120],
        version=str(metadata.get("skill_version") or settings.default_candidate_version),
        description=str(data.get("description") or data.get("text") or ""),
        trigger_conditions=list(metadata.get("trigger_conditions") or []),
        required_capabilities=list(metadata.get("required_capabilities") or []),
        contraindications=list(metadata.get("contraindications") or []),
        source_evidence_refs=refs,
        source_candidate_id=candidate_id,
        actor="activity_normalizer",
        is_fixture=bool(metadata.get("is_fixture", False)),
        metadata={
            "extractor_id": data.get("extractor_id"),
            "extractor_version": data.get("extractor_version"),
            "extraction_config_id": data.get("extraction_config_id"),
        },
        reader=ctx.view,
    )


@behavior(
    name="skill_evaluation_linker",
    on=["object.created"],
    where={"object.type": "evaluation"},
    view={"include_types": ["skill_usage", "skill_evaluation_link"]},
    creates=["skill_evaluation_link"],
)
def skill_evaluation_linker(event, graph, ctx, *, settings: SkillsSettings):
    """Link a Core evaluation carrying usage provenance to that exact usage."""

    if not settings.enabled:
        return
    wrapper = event.payload.get("object", {})
    evaluation_id = wrapper.get("id")
    data = wrapper.get("data", {})
    usage_id = str((data.get("metadata") or {}).get("usage_id") or "")
    if not evaluation_id or not usage_id:
        return
    link_skill_evaluation_fn(
        graph,
        usage_id,
        evaluation_id,
        reader=ctx.view,
    )


@behavior(
    name="skill_reliability_eligibility",
    on=["reliability.changed"],
    creates=[],
)
def skill_reliability_eligibility(event, graph, ctx, *, settings: SkillsSettings):
    """Apply generic reliability to eligibility without deleting skill history."""

    if not settings.enabled:
        return
    payload = event.payload or {}
    if payload.get("artifact_type") != "skill_version":
        return
    skill = graph.get_object(str(payload.get("artifact_id") or ""))
    if skill is None or skill.type != "skill":
        return
    verdict = payload.get("verdict")
    outcome_event_id = payload.get("outcome_event_id")
    current = skill.data.get("status")
    metadata = dict(skill.data.get("metadata") or {})
    if verdict in {"harmful", "stale"} and current in {"candidate", "promoted"}:
        _emit_event(
            graph,
            "skill.demoted",
            {
                "artifact_id": skill.id,
                "skill_id": skill.data["skill_id"],
                "skill_version": skill.data["version"],
                "reason": f"reliability became {verdict}",
                "actor": "eval_outcome",
                "outcome_evidence_event_id": outcome_event_id,
                "is_fixture": skill.data.get("is_fixture", False),
            },
        )
        metadata["demoted_by_reliability"] = True
        graph.patch_object(
            skill.id,
            {
                "status": "demoted",
                "demotion_reason": f"reliability became {verdict}",
                "metadata": metadata,
            },
        )
    elif (
        verdict == "supported"
        and current == "demoted"
        and metadata.get("demoted_by_reliability")
    ):
        promoted = _emit_event(
            graph,
            "skill.promoted",
            {
                "artifact_id": skill.id,
                "skill_id": skill.data["skill_id"],
                "skill_version": skill.data["version"],
                "evidence_id": outcome_event_id,
                "rationale": "reliability recovered to supported",
                "actor": "eval_outcome",
                "prior_status": "demoted",
                "reliability_basis": "outcome projection",
                "is_fixture": skill.data.get("is_fixture", False),
            },
        )
        history = list(skill.data.get("promotion_history") or [])
        history.append(
            {
                "event_id": getattr(promoted, "id", None),
                "evidence_id": outcome_event_id,
                "rationale": "reliability recovered to supported",
                "actor": "eval_outcome",
                "prior_status": "demoted",
                "new_status": "promoted",
            }
        )
        metadata["demoted_by_reliability"] = False
        graph.patch_object(
            skill.id,
            {
                "status": "promoted",
                "demotion_reason": None,
                "promotion_history": history,
                "metadata": metadata,
            },
        )


@behavior(
    name="skill_outcome_summary",
    on=["outcome.helped", "outcome.hurt", "outcome.neutral"],
    creates=[],
)
def skill_outcome_summary(event, graph, ctx, *, settings: SkillsSettings):
    """Maintain the skill object's neutral outcome tally from canonical outcomes."""

    if not settings.enabled:
        return
    payload = event.payload or {}
    if payload.get("artifact_type") != "skill_version":
        return
    skill = graph.get_object(str(payload.get("artifact_id") or ""))
    if skill is None or skill.type != "skill":
        return
    kind = event.type.removeprefix("outcome.")
    summary = dict(skill.data.get("outcome_summary") or {})
    summary[kind] = int(summary.get(kind, 0)) + 1
    graph.patch_object(skill.id, {"outcome_summary": summary})


BEHAVIORS = [
    skill_candidate_author,
    skill_evaluation_linker,
    skill_reliability_eligibility,
    skill_outcome_summary,
]
