"""Deterministic outcome capture, correction, and reliability queries."""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from activegraph import Event
from activegraph.packs import tool

from .object_types import OutcomeRecord


TERMINAL_EVENTS = frozenset({"outcome.helped", "outcome.hurt", "outcome.neutral"})
MAINTENANCE_EVENTS = frozenset(
    {"outcome.contradicted", "outcome.stale", "outcome.superseded"}
)
OUTCOME_EVENTS = TERMINAL_EVENTS | MAINTENANCE_EVENTS


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()}"


def _objects(reader, object_type: str) -> list[Any]:
    try:
        return list(reader.objects(type=object_type))
    except Exception:
        return []


def _get_object(reader, object_id: str):
    try:
        return reader.get_object(object_id)
    except Exception:
        return None


def _emit_event(graph, event_type: str, payload: dict[str, Any], actor: str):
    if not hasattr(graph, "ids"):
        return graph.emit(event_type, payload)
    event = Event(
        id=graph.ids.event(),
        type=event_type,
        payload=payload,
        actor=actor,
        timestamp=graph.clock.now(),
    )
    graph.emit(event)
    return event


def _find_usage(reader, usage_id: str):
    return next(
        (
            obj
            for obj in _objects(reader, "skill_usage")
            if obj.data.get("usage_id") == usage_id
        ),
        None,
    )


def _subject_trace(
    reader,
    evaluation,
    *,
    usage_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
    artifact_type: Optional[str] = None,
    artifact_version: Optional[str] = None,
) -> dict[str, Any]:
    data = evaluation.data
    metadata = dict(data.get("metadata") or {})
    resolved_usage_id = usage_id or metadata.get("usage_id")
    usage = _find_usage(reader, str(resolved_usage_id or "")) if resolved_usage_id else None
    if usage is None and data.get("subject_type") == "skill_usage":
        candidate = _get_object(reader, str(data.get("subject_id") or ""))
        if candidate is not None and candidate.type == "skill_usage":
            usage = candidate
            resolved_usage_id = candidate.data.get("usage_id")

    resolved_artifact_id = artifact_id or metadata.get("artifact_id")
    resolved_artifact_type = artifact_type or metadata.get("artifact_type")
    resolved_artifact_version = artifact_version or metadata.get("artifact_version")
    if usage is not None:
        resolved_artifact_id = resolved_artifact_id or usage.data.get("skill_version_id")
        resolved_artifact_type = resolved_artifact_type or "skill_version"
        resolved_artifact_version = resolved_artifact_version or usage.data.get("skill_version")
    if not resolved_artifact_id:
        resolved_artifact_id = str(data.get("subject_id") or "")
    if not resolved_artifact_type:
        resolved_artifact_type = str(data.get("subject_type") or "")
    if not resolved_artifact_id or not resolved_artifact_type:
        raise ValueError("outcome requires an evaluation/usage/artifact subject trace")
    return {
        "usage_id": str(resolved_usage_id) if resolved_usage_id else None,
        "artifact_id": str(resolved_artifact_id),
        "artifact_type": str(resolved_artifact_type),
        "artifact_version": (
            str(resolved_artifact_version) if resolved_artifact_version is not None else None
        ),
        "usage": usage,
    }


def _terminal_record(reader, evaluation_id: str):
    return next(
        (
            record
            for record in _objects(reader, "outcome_record")
            if record.data.get("evaluation_id") == evaluation_id
            and f"outcome.{record.data.get('outcome_type')}" in TERMINAL_EVENTS
        ),
        None,
    )


def record_terminal_outcome_fn(
    graph,
    event_type: str,
    evaluation_id: str,
    rationale: str,
    actor: str,
    *,
    usage_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
    artifact_type: Optional[str] = None,
    artifact_version: Optional[str] = None,
    source_context: Optional[dict[str, Any]] = None,
    is_fixture: bool = False,
    reader=None,
) -> dict[str, Any]:
    """Record exactly one terminal value for an evaluation."""

    if not event_type.startswith("outcome."):
        event_type = f"outcome.{event_type}"
    if event_type not in TERMINAL_EVENTS:
        raise ValueError("terminal outcome must be helped, hurt, or neutral")
    read_view = reader or graph
    evaluation = _get_object(read_view, evaluation_id) or _get_object(graph, evaluation_id)
    if evaluation is None or evaluation.type != "evaluation":
        raise ValueError("evaluation_id must reference a Core evaluation")
    existing = _terminal_record(read_view, evaluation_id)
    if existing is not None:
        existing_type = f"outcome.{existing.data.get('outcome_type')}"
        if existing_type != event_type:
            raise ValueError(
                "evaluation already has a terminal outcome; correction requires supersession"
            )
        return {"ok": True, "created": False, "record": existing, "evaluation": evaluation}

    trace = _subject_trace(
        read_view,
        evaluation,
        usage_id=usage_id,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        artifact_version=artifact_version,
    )
    payload = {
        "evaluation_id": evaluation_id,
        "usage_id": trace["usage_id"],
        "artifact_id": trace["artifact_id"],
        "artifact_type": trace["artifact_type"],
        "artifact_version": trace["artifact_version"],
        "rationale": rationale,
        "actor": actor,
        "source_context": source_context or {},
        "source_surface_id": (source_context or {}).get("source_surface_id"),
        "source_category": (source_context or {}).get("source_category"),
        "contribution_key": f"terminal:{evaluation_id}",
        "is_fixture": is_fixture,
    }
    outcome = _emit_event(graph, event_type, payload, actor)
    record = graph.add_object(
        "outcome_record",
        OutcomeRecord(
            outcome_event_id=getattr(outcome, "id", "") or "behavior-emitted",
            outcome_type=event_type.removeprefix("outcome."),
            evaluation_id=evaluation_id,
            usage_id=trace["usage_id"],
            artifact_id=trace["artifact_id"],
            artifact_type=trace["artifact_type"],
            artifact_version=trace["artifact_version"],
            contribution_key=f"terminal:{evaluation_id}",
            rationale=rationale,
            actor=actor,
            source_context=source_context or {},
            is_fixture=is_fixture,
        ).model_dump(),
    )
    try:
        graph.add_relation(record.id, evaluation_id, "records_evaluation")
    except Exception:
        pass
    return {"ok": True, "created": True, "record": record, "evaluation": evaluation}


def record_explicit_verdict_fn(
    graph,
    verdict: str,
    rationale: str,
    actor: str,
    *,
    evaluation_id: Optional[str] = None,
    usage_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
    artifact_type: Optional[str] = None,
    artifact_version: Optional[str] = None,
    source_context: Optional[dict[str, Any]] = None,
    is_fixture: bool = False,
) -> dict[str, Any]:
    """Capture an explicit accept/reject/neutral verdict with no model judgment."""

    mapping = {
        "accept": ("outcome.helped", "accepted"),
        "reject": ("outcome.hurt", "rejected"),
        "neutral": ("outcome.neutral", "completed_neutrally"),
    }
    if verdict not in mapping:
        raise ValueError("verdict must be accept, reject, or neutral")
    event_type, judgment = mapping[verdict]
    if evaluation_id is None:
        usage = _find_usage(graph, str(usage_id or "")) if usage_id else None
        if usage is not None:
            subject_id = usage.id
            subject_type = "skill_usage"
            artifact_id = artifact_id or usage.data.get("skill_version_id")
            artifact_type = artifact_type or "skill_version"
            artifact_version = artifact_version or usage.data.get("skill_version")
            is_fixture = is_fixture or bool(usage.data.get("is_fixture", False))
        elif artifact_id and artifact_type:
            subject_id = artifact_id
            subject_type = artifact_type
        else:
            raise ValueError("explicit verdict requires evaluation_id, usage_id, or artifact subject")
        evaluation = graph.add_object(
            "evaluation",
            {
                "subject_id": subject_id,
                "subject_type": subject_type,
                "judgment": judgment,
                "rationale": rationale,
                "evaluator": actor,
                "metadata": {
                    "usage_id": usage_id,
                    "artifact_id": artifact_id,
                    "artifact_type": artifact_type,
                    "artifact_version": artifact_version,
                    "capture_outcome": True,
                },
            },
        )
        evaluation_id = evaluation.id
    result = record_terminal_outcome_fn(
        graph,
        event_type,
        evaluation_id,
        rationale,
        actor,
        usage_id=usage_id,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        artifact_version=artifact_version,
        source_context=source_context,
        is_fixture=is_fixture,
    )
    result["evaluation_id"] = evaluation_id
    return result


def record_maintenance_outcome_fn(
    graph,
    event_type: str,
    artifact_id: str,
    artifact_type: str,
    rationale: str,
    actor: str,
    *,
    artifact_version: Optional[str] = None,
    evidence_revision_id: Optional[str] = None,
    superseding_version: Optional[str] = None,
    evaluation_id: Optional[str] = None,
    usage_id: Optional[str] = None,
    source_context: Optional[dict[str, Any]] = None,
    is_fixture: bool = False,
    reader=None,
) -> dict[str, Any]:
    """Record one idempotent contradicted, stale, or superseded outcome."""

    if not event_type.startswith("outcome."):
        event_type = f"outcome.{event_type}"
    if event_type not in MAINTENANCE_EVENTS:
        raise ValueError("maintenance outcome must be contradicted, stale, or superseded")
    if not artifact_id or not artifact_type:
        raise ValueError("maintenance outcome requires an artifact subject")
    if event_type in {"outcome.contradicted", "outcome.stale"} and not evidence_revision_id:
        raise ValueError("contradicted/stale outcomes require evidence_revision_id")
    if event_type == "outcome.superseded" and not superseding_version:
        raise ValueError("superseded outcome requires superseding_version")
    subject_key = evidence_revision_id or superseding_version
    contribution_key = f"{artifact_id}:{subject_key}:{event_type}"
    read_view = reader or graph
    existing = next(
        (
            obj
            for obj in _objects(read_view, "outcome_record")
            if obj.data.get("contribution_key") == contribution_key
        ),
        None,
    )
    if existing is not None:
        return {"ok": True, "created": False, "record": existing}
    if evaluation_id:
        evaluation = _get_object(read_view, evaluation_id) or _get_object(graph, evaluation_id)
        if evaluation is None or evaluation.type != "evaluation":
            raise ValueError("evaluation_id must reference a Core evaluation")
    payload = {
        "evaluation_id": evaluation_id,
        "usage_id": usage_id,
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "artifact_version": artifact_version,
        "evidence_revision_id": evidence_revision_id,
        "superseding_version": superseding_version,
        "rationale": rationale,
        "actor": actor,
        "source_context": source_context or {},
        "contribution_key": contribution_key,
        "is_fixture": is_fixture,
    }
    outcome = _emit_event(graph, event_type, payload, actor)
    record = graph.add_object(
        "outcome_record",
        OutcomeRecord(
            outcome_event_id=getattr(outcome, "id", "") or "behavior-emitted",
            outcome_type=event_type.removeprefix("outcome."),
            evaluation_id=evaluation_id,
            usage_id=usage_id,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            artifact_version=artifact_version,
            evidence_revision_id=evidence_revision_id,
            superseding_version=superseding_version,
            contribution_key=contribution_key,
            rationale=rationale,
            actor=actor,
            source_context=source_context or {},
            is_fixture=is_fixture,
        ).model_dump(),
    )
    if evaluation_id:
        try:
            graph.add_relation(record.id, evaluation_id, "records_evaluation")
        except Exception:
            pass
    return {"ok": True, "created": True, "record": record}


def supersede_evaluation_fn(
    graph,
    prior_evaluation_id: str,
    replacement_verdict: str,
    rationale: str,
    actor: str,
) -> dict[str, Any]:
    """Correct an evaluation through replacement plus outcome.superseded."""

    prior = graph.get_object(prior_evaluation_id)
    if prior is None or prior.type != "evaluation":
        raise ValueError("prior_evaluation_id must reference a Core evaluation")
    prior_terminal = _terminal_record(graph, prior_evaluation_id)
    if prior_terminal is None:
        raise ValueError("the prior evaluation has no terminal outcome to supersede")
    mapping = {
        "accept": ("outcome.helped", "accepted"),
        "reject": ("outcome.hurt", "rejected"),
        "neutral": ("outcome.neutral", "completed_neutrally"),
    }
    if replacement_verdict not in mapping:
        raise ValueError("replacement_verdict must be accept, reject, or neutral")
    replacement_event_type, judgment = mapping[replacement_verdict]
    pdata = prior_terminal.data
    replacement = graph.add_object(
        "evaluation",
        {
            "subject_id": prior_evaluation_id,
            "subject_type": "evaluation",
            "judgment": judgment,
            "rationale": rationale,
            "evaluator": actor,
            "metadata": {
                "supersedes_evaluation_id": prior_evaluation_id,
                "artifact_id": pdata["artifact_id"],
                "artifact_type": pdata["artifact_type"],
                "artifact_version": pdata.get("artifact_version"),
            },
        },
    )
    superseded = record_maintenance_outcome_fn(
        graph,
        "outcome.superseded",
        pdata["artifact_id"],
        pdata["artifact_type"],
        rationale,
        actor,
        artifact_version=pdata.get("artifact_version"),
        superseding_version=replacement.id,
        evaluation_id=prior_evaluation_id,
        usage_id=pdata.get("usage_id"),
        is_fixture=bool(pdata.get("is_fixture", False)),
    )
    terminal = record_terminal_outcome_fn(
        graph,
        replacement_event_type,
        replacement.id,
        rationale,
        actor,
        artifact_id=pdata["artifact_id"],
        artifact_type=pdata["artifact_type"],
        artifact_version=pdata.get("artifact_version"),
        is_fixture=bool(pdata.get("is_fixture", False)),
    )
    return {
        "ok": True,
        "replacement_evaluation": replacement,
        "supersession": superseded["record"],
        "terminal": terminal["record"],
    }


def get_reliability_fn(graph, artifact_id: str) -> Optional[dict[str, Any]]:
    reliability = next(
        (
            obj
            for obj in reversed(_objects(graph, "artifact_reliability"))
            if obj.data.get("artifact_id") == artifact_id
        ),
        None,
    )
    return {"object_id": reliability.id, **reliability.data} if reliability else None


@tool(name="record_explicit_verdict", description="Record an explicit accept, reject, or neutral outcome.", deterministic=True)
def record_explicit_verdict(
    graph,
    verdict: str,
    rationale: str = "",
    actor: str = "owner",
    evaluation_id: Optional[str] = None,
    usage_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
    artifact_type: Optional[str] = None,
    artifact_version: Optional[str] = None,
    source_context: Optional[dict[str, Any]] = None,
    is_fixture: bool = False,
) -> dict[str, Any]:
    return record_explicit_verdict_fn(
        graph,
        verdict,
        rationale,
        actor,
        evaluation_id=evaluation_id,
        usage_id=usage_id,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        artifact_version=artifact_version,
        source_context=source_context,
        is_fixture=is_fixture,
    )


@tool(name="record_maintenance_outcome", description="Record contradicted, stale, or superseded artifact evidence.", deterministic=True)
def record_maintenance_outcome(
    graph,
    event_type: str,
    artifact_id: str = "",
    artifact_type: str = "",
    rationale: str = "",
    actor: str = "owner",
    artifact_version: Optional[str] = None,
    evidence_revision_id: Optional[str] = None,
    superseding_version: Optional[str] = None,
    evaluation_id: Optional[str] = None,
    usage_id: Optional[str] = None,
    source_context: Optional[dict[str, Any]] = None,
    is_fixture: bool = False,
) -> dict[str, Any]:
    return record_maintenance_outcome_fn(
        graph,
        event_type,
        artifact_id,
        artifact_type,
        rationale,
        actor,
        artifact_version=artifact_version,
        evidence_revision_id=evidence_revision_id,
        superseding_version=superseding_version,
        evaluation_id=evaluation_id,
        usage_id=usage_id,
        source_context=source_context,
        is_fixture=is_fixture,
    )


@tool(name="supersede_evaluation", description="Correct a terminal evaluation through explicit supersession.", deterministic=True)
def supersede_evaluation(
    graph,
    prior_evaluation_id: str,
    replacement_verdict: str = "neutral",
    rationale: str = "",
    actor: str = "owner",
) -> dict[str, Any]:
    return supersede_evaluation_fn(
        graph, prior_evaluation_id, replacement_verdict, rationale, actor
    )


@tool(name="get_artifact_reliability", description="Read the current artifact-owned reliability projection.", deterministic=True)
def get_artifact_reliability(graph, artifact_id: str) -> Optional[dict[str, Any]]:
    return get_reliability_fn(graph, artifact_id)


TOOLS = [
    record_explicit_verdict,
    record_maintenance_outcome,
    supersede_evaluation,
    get_artifact_reliability,
]


__all__ = [
    "TERMINAL_EVENTS",
    "MAINTENANCE_EVENTS",
    "OUTCOME_EVENTS",
    "TOOLS",
    "record_terminal_outcome_fn",
    "record_explicit_verdict_fn",
    "record_maintenance_outcome_fn",
    "supersede_evaluation_fn",
    "get_reliability_fn",
]
