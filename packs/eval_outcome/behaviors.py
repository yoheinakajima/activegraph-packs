"""Thin capture behaviors and the artifact reliability projector."""

from __future__ import annotations

from activegraph.packs import behavior

from .object_types import ArtifactReliability
from .settings import EvalOutcomeSettings
from .tools import (
    OUTCOME_EVENTS,
    _emit_event,
    record_maintenance_outcome_fn,
    record_terminal_outcome_fn,
)


def _objects(reader, object_type: str):
    try:
        return list(reader.objects(type=object_type))
    except Exception:
        return []


def _verdict_and_multiplier(event_type: str, settings: EvalOutcomeSettings):
    if event_type == "outcome.helped":
        return "supported", settings.supported_retrieval_multiplier
    if event_type in {"outcome.hurt", "outcome.contradicted"}:
        return "harmful", settings.harmful_retrieval_multiplier
    if event_type in {"outcome.stale", "outcome.superseded"}:
        return "stale", settings.stale_retrieval_multiplier
    return "weak", settings.weak_retrieval_multiplier


@behavior(
    name="artifact_reliability_projector",
    on=sorted(OUTCOME_EVENTS),
    view={"include_types": ["artifact_reliability", "outcome_record"]},
    creates=["artifact_reliability"],
)
def artifact_reliability_projector(
    event,
    graph,
    ctx,
    *,
    settings: EvalOutcomeSettings,
):
    """Project recency-aware artifact reliability from canonical outcomes."""

    if not settings.enabled:
        return
    payload = event.payload or {}
    artifact_id = str(payload.get("artifact_id") or "")
    artifact_type = str(payload.get("artifact_type") or "")
    if not artifact_id or not artifact_type:
        return
    existing = next(
        (
            obj
            for obj in _objects(ctx.view, "artifact_reliability")
            if obj.data.get("artifact_id") == artifact_id
        ),
        None,
    )
    tallies = {
        "helped": 0,
        "hurt": 0,
        "neutral": 0,
        "contradicted": 0,
        "stale": 0,
        "superseded": 0,
    }
    evidence_event_ids = []
    if existing is not None:
        tallies.update(existing.data.get("tallies") or {})
        evidence_event_ids = list(existing.data.get("evidence_event_ids") or [])
    kind = event.type.removeprefix("outcome.")
    tallies[kind] = int(tallies.get(kind, 0)) + 1
    if event.id not in evidence_event_ids:
        evidence_event_ids.append(event.id)
    verdict, multiplier = _verdict_and_multiplier(event.type, settings)
    update = {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "artifact_version": payload.get("artifact_version"),
        "tallies": tallies,
        "verdict": verdict,
        "eligible": verdict not in {"harmful", "stale"},
        "retrieval_multiplier": multiplier,
        "latest_outcome_type": kind,
        "latest_outcome_event_id": event.id,
        "latest_evaluation_id": payload.get("evaluation_id"),
        "latest_usage_id": payload.get("usage_id"),
        "evidence_event_ids": evidence_event_ids,
        "metadata": {
            "latest_rationale": payload.get("rationale", ""),
            "latest_actor": payload.get("actor", event.actor),
        },
    }
    _emit_event(
        graph,
        "reliability.changed",
        {
            **update,
            "outcome_event_id": event.id,
            "evidence_revision_id": payload.get("evidence_revision_id"),
        },
        "eval_outcome",
    )
    if existing is None:
        reliability = graph.add_object(
            "artifact_reliability",
            ArtifactReliability(**update).model_dump(),
        )
    else:
        graph.patch_object(existing.id, update)
        reliability = graph.get_object(existing.id)
    outcome_record = next(
        (
            obj
            for obj in _objects(ctx.view, "outcome_record")
            if obj.data.get("outcome_event_id") == event.id
        ),
        None,
    )
    if outcome_record is not None:
        try:
            graph.add_relation(outcome_record.id, reliability.id, "updates_reliability")
        except Exception:
            pass


_TERMINAL_JUDGMENTS = {
    "accepted": "outcome.helped",
    "completed_successfully": "outcome.helped",
    "done": "outcome.helped",
    "rejected": "outcome.hurt",
    "failed": "outcome.hurt",
    "completed_neutrally": "outcome.neutral",
    "neutral": "outcome.neutral",
    "not_relevant": "outcome.neutral",
}


@behavior(
    name="evaluation_outcome_capture",
    on=["object.created"],
    where={"object.type": "evaluation"},
    view={
        "include_types": [
            "outcome_record",
            "skill_usage",
            "task",
            "capability_result",
            "memory_item",
        ]
    },
    creates=["outcome_record"],
)
def evaluation_outcome_capture(event, graph, ctx, *, settings: EvalOutcomeSettings):
    """Capture deterministic gateway, task, skill, and contradiction judgments."""

    if not settings.enabled:
        return
    wrapper = event.payload.get("object", {})
    evaluation_id = wrapper.get("id")
    data = wrapper.get("data", {})
    if not evaluation_id:
        return
    subject_type = str(data.get("subject_type") or "")
    judgment = str(data.get("judgment") or "")
    metadata = dict(data.get("metadata") or {})
    if judgment == "contradicted" and subject_type in {"memory_item", "memory_claim"}:
        evidence_revision_id = metadata.get("evidence_revision_id")
        if evidence_revision_id:
            record_maintenance_outcome_fn(
                graph,
                "outcome.contradicted",
                str(data.get("subject_id") or ""),
                subject_type,
                str(data.get("rationale") or "contradiction finding"),
                str(data.get("evaluator") or "memory_layer"),
                artifact_version=metadata.get("artifact_version"),
                evidence_revision_id=str(evidence_revision_id),
                evaluation_id=evaluation_id,
                source_context=dict(metadata.get("source_context") or {}),
                reader=ctx.view,
            )
        return
    allowed_subject = subject_type in {"task", "capability_result"} or (
        subject_type == "skill_usage" and metadata.get("capture_outcome")
    )
    event_type = _TERMINAL_JUDGMENTS.get(judgment)
    if not allowed_subject or event_type is None:
        return
    record_terminal_outcome_fn(
        graph,
        event_type,
        evaluation_id,
        str(data.get("rationale") or judgment),
        str(data.get("evaluator") or "evaluation_capture"),
        usage_id=metadata.get("usage_id"),
        artifact_id=metadata.get("artifact_id"),
        artifact_type=metadata.get("artifact_type"),
        artifact_version=metadata.get("artifact_version"),
        source_context=dict(metadata.get("source_context") or {}),
        is_fixture=bool(metadata.get("is_fixture", False)),
        reader=ctx.view,
    )


BEHAVIORS = [artifact_reliability_projector, evaluation_outcome_capture]
