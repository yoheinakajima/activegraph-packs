"""Mutation and query capabilities for governed skill artifacts."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Optional

from activegraph import Event
from activegraph.packs import tool

from .object_types import (
    SkillArtifact,
    SkillEvaluationLink,
    SkillPromotionEvidence,
    SkillUsage,
)


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()}"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _emit_event(graph, event_type: str, payload: dict[str, Any]):
    """Emit through raw Graph or constrained BehaviorGraph."""

    if not hasattr(graph, "ids"):
        return graph.emit(event_type, payload)
    event = Event(
        id=graph.ids.event(),
        type=event_type,
        payload=payload,
        actor="skills",
        timestamp=graph.clock.now(),
    )
    graph.emit(event)
    return event


def _objects(reader, object_type: str) -> list[Any]:
    try:
        return list(reader.objects(type=object_type))
    except Exception:
        return []


def _find_skill(reader, name: str, version: str):
    return next(
        (
            obj
            for obj in _objects(reader, "skill")
            if obj.data.get("name") == name and obj.data.get("version") == version
        ),
        None,
    )


def _find_skill_by_object_or_identity(reader, artifact_id: str):
    try:
        obj = reader.get_object(artifact_id)
    except Exception:
        obj = None
    if obj is not None and getattr(obj, "type", None) == "skill":
        return obj
    return next(
        (
            candidate
            for candidate in _objects(reader, "skill")
            if candidate.data.get("version_identity") == artifact_id
        ),
        None,
    )


def _definition(
    name: str,
    version: str,
    description: str,
    trigger_conditions: Iterable[str],
    required_capabilities: Iterable[str],
    contraindications: Iterable[str],
) -> dict[str, Any]:
    return {
        "name": name.strip(),
        "version": version.strip(),
        "description": description.strip(),
        "trigger_conditions": sorted({str(item).strip() for item in trigger_conditions if str(item).strip()}),
        "required_capabilities": sorted(
            {str(item).strip() for item in required_capabilities if str(item).strip()}
        ),
        "contraindications": sorted(
            {str(item).strip() for item in contraindications if str(item).strip()}
        ),
    }


def author_skill_fn(
    graph,
    name: str,
    version: str,
    description: str = "",
    trigger_conditions: Optional[list[str]] = None,
    required_capabilities: Optional[list[str]] = None,
    contraindications: Optional[list[str]] = None,
    source_evidence_refs: Optional[list[str]] = None,
    source_candidate_id: Optional[str] = None,
    actor: str = "owner",
    is_fixture: bool = False,
    metadata: Optional[dict[str, Any]] = None,
    *,
    reader=None,
) -> dict[str, Any]:
    """Author one immutable definition version with mandatory provenance."""

    read_view = reader or graph
    definition = _definition(
        name,
        version,
        description,
        trigger_conditions or [],
        required_capabilities or [],
        contraindications or [],
    )
    if not definition["name"]:
        raise ValueError("skill name is required")
    evidence_refs = sorted(
        {str(ref).strip() for ref in (source_evidence_refs or []) if str(ref).strip()}
    )
    if not evidence_refs:
        raise ValueError("skill proposal requires source evidence provenance")

    skill_id = _stable_id("skill", definition["name"].casefold())
    version_identity = _stable_id("skill_version", skill_id, definition["version"])
    definition_hash = hashlib.sha256(_canonical_json(definition).encode("utf-8")).hexdigest()
    existing = _find_skill(read_view, definition["name"], definition["version"])
    if existing is not None:
        if existing.data.get("definition_hash") != definition_hash:
            raise ValueError(
                "skill version identity already exists with a different definition; "
                "author a materially new semantic version"
            )
        return {"ok": True, "created": False, "skill": existing}

    proposed = _emit_event(
        graph,
        "skill.proposed",
        {
            "artifact_id": version_identity,
            "skill_id": skill_id,
            "skill_version": definition["version"],
            "name": definition["name"],
            "source_evidence_refs": evidence_refs,
            "source_candidate_id": source_candidate_id,
            "actor": actor,
            "is_fixture": is_fixture,
        },
    )
    skill = graph.add_object(
        "skill",
        SkillArtifact(
            skill_id=skill_id,
            version_identity=version_identity,
            definition_hash=definition_hash,
            source_evidence_refs=evidence_refs,
            source_candidate_id=source_candidate_id,
            is_fixture=is_fixture,
            metadata={
                **(metadata or {}),
                "proposed_event_id": getattr(proposed, "id", None),
                "proposed_by": actor,
            },
            **definition,
        ).model_dump(),
    )
    return {"ok": True, "created": True, "skill": skill}


def invoke_skill_fn(
    graph,
    name: str,
    version: str,
    usage_id: str,
    execution_ref: str,
    execution_kind: str = "trial",
    actor: str = "agent",
    source_context: Optional[dict[str, Any]] = None,
    capability_requests: Optional[list[dict[str, Any]]] = None,
    is_fixture: bool = False,
    allow_candidate_trials: bool = True,
) -> dict[str, Any]:
    """Invoke one exact version once; retries return the original usage."""

    if execution_kind not in {"real", "trial"}:
        raise ValueError("execution_kind must be 'real' or 'trial'")
    if not usage_id.strip() or not execution_ref.strip() or not actor.strip():
        raise ValueError("usage_id, execution_ref, and actor are required")
    skill = _find_skill(graph, name, version)
    if skill is None:
        raise ValueError(f"unknown skill version {name!r}@{version}")

    existing = next(
        (obj for obj in _objects(graph, "skill_usage") if obj.data.get("usage_id") == usage_id),
        None,
    )
    if existing is not None:
        same = (
            existing.data.get("skill_version_id") == skill.id
            and existing.data.get("execution_ref") == execution_ref
        )
        if not same:
            raise ValueError("usage_id already belongs to a different execution or skill version")
        return {"ok": True, "created": False, "usage": existing, "capability_calls": []}

    status = skill.data.get("status")
    if status in {"demoted", "disabled"}:
        raise ValueError(f"skill version is not eligible: {status}")
    if execution_kind == "real" and status != "promoted":
        raise ValueError("real invocation requires a promoted skill version")
    if execution_kind == "trial" and status == "candidate" and not allow_candidate_trials:
        raise ValueError("candidate trial invocation is disabled")

    requests = capability_requests or []
    required = list(skill.data.get("required_capabilities") or [])
    for request in requests:
        key = str(request.get("capability_key") or "")
        if key not in required:
            raise ValueError(f"capability request {key!r} is not declared by the skill")
        provider = graph.get_object(str(request.get("provider_id") or ""))
        if provider is None or provider.type != "capability_provider":
            raise ValueError(f"capability request {key!r} has no valid provider")

    used = _emit_event(
        graph,
        "skill.used",
        {
            "usage_id": usage_id,
            "skill_id": skill.data["skill_id"],
            "skill_version": skill.data["version"],
            "skill_version_id": skill.id,
            "execution_ref": execution_ref,
            "execution_kind": execution_kind,
            "actor": actor,
            "source_context": source_context or {},
            "required_capabilities": required,
            "is_fixture": is_fixture,
        },
    )
    usage = graph.add_object(
        "skill_usage",
        SkillUsage(
            usage_id=usage_id,
            skill_id=skill.data["skill_id"],
            skill_version=skill.data["version"],
            skill_version_id=skill.id,
            execution_ref=execution_ref,
            execution_kind=execution_kind,
            actor=actor,
            source_context=source_context or {},
            required_capabilities=required,
            used_event_id=getattr(used, "id", "") or "behavior-emitted",
            is_fixture=is_fixture,
        ).model_dump(),
    )
    graph.add_relation(usage.id, skill.id, "usage_of")

    capability_calls = []
    for request in requests:
        provider_id = str(request["provider_id"])
        key = str(request["capability_key"])
        provider_name, _, capability_name = key.partition(".")
        call = graph.add_object(
            "capability_call",
            {
                "provider_id": provider_id,
                "provider_name": provider_name,
                "capability_name": capability_name or key,
                "input_data": dict(request.get("input_data") or {}),
                "credential_ref_name": request.get("credential_ref_name"),
                "credential_ref_id": request.get("credential_ref_id"),
                "risk_class": request.get("risk_class", "medium"),
                "status": "proposed",
                "proposed_by": f"skills:{skill.data['version_identity']}",
                "frame_id": (source_context or {}).get("frame_id"),
                "metadata": {
                    "skill_usage_id": usage_id,
                    "skill_version_id": skill.id,
                },
            },
        )
        capability_calls.append(call)
    if capability_calls:
        graph.patch_object(
            usage.id,
            {"capability_call_ids": [call.id for call in capability_calls]},
        )
        usage = graph.get_object(usage.id)
    if not skill.data.get("definition_locked", False):
        graph.patch_object(skill.id, {"definition_locked": True})
    return {
        "ok": True,
        "created": True,
        "usage": usage,
        "capability_calls": capability_calls,
    }


def record_promotion_evidence_fn(
    graph,
    skill_version_id: str,
    kind: str,
    reference_ids: list[str],
    rationale: str = "",
    actor: str = "",
    accepted: bool = True,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Record evidence without changing eligibility."""

    skill = _find_skill_by_object_or_identity(graph, skill_version_id)
    if skill is None:
        raise ValueError("unknown skill version")
    refs = sorted({str(ref).strip() for ref in reference_ids if str(ref).strip()})
    if not refs:
        raise ValueError("promotion evidence requires reference ids")
    if kind == "repeated_accepted_use" and len(refs) < 2:
        raise ValueError("repeated accepted use requires at least two distinct references")
    if kind == "owner_approval" and (not actor.strip() or not rationale.strip()):
        raise ValueError("owner approval requires actor and recorded rationale")
    evidence_id = _stable_id("skill_evidence", skill.id, kind, _canonical_json(refs))
    existing = next(
        (
            obj
            for obj in _objects(graph, "skill_promotion_evidence")
            if obj.data.get("evidence_id") == evidence_id
        ),
        None,
    )
    if existing is not None:
        return {"ok": True, "created": False, "evidence": existing}
    evidence = graph.add_object(
        "skill_promotion_evidence",
        SkillPromotionEvidence(
            evidence_id=evidence_id,
            skill_version_id=skill.id,
            kind=kind,
            reference_ids=refs,
            accepted=accepted,
            rationale=rationale,
            actor=actor,
            metadata=metadata or {},
        ).model_dump(),
    )
    graph.add_relation(evidence.id, skill.id, "promotion_for")
    return {"ok": True, "created": True, "evidence": evidence}


def _promotion_support(graph, skill, evidence) -> tuple[bool, str]:
    reliability = [
        obj
        for obj in _objects(graph, "artifact_reliability")
        if obj.data.get("artifact_id") in {skill.id, skill.data.get("version_identity")}
    ]
    if reliability:
        verdict = reliability[-1].data.get("verdict", "weak")
        return verdict == "supported", f"artifact reliability is {verdict}"
    if evidence.data.get("accepted"):
        return True, f"accepted {evidence.data.get('kind')} evidence"
    return False, "recorded evidence was not accepted"


def promote_skill_fn(
    graph,
    skill_version_id: str,
    evidence_id: str,
    rationale: str,
    actor: str = "owner",
) -> dict[str, Any]:
    """Promote only from accepted proof and a supporting reliability state."""

    skill = _find_skill_by_object_or_identity(graph, skill_version_id)
    if skill is None:
        raise ValueError("unknown skill version")
    evidence = next(
        (
            obj
            for obj in _objects(graph, "skill_promotion_evidence")
            if obj.data.get("evidence_id") == evidence_id
            and obj.data.get("skill_version_id") == skill.id
        ),
        None,
    )
    if evidence is None:
        raise ValueError("promotion requires recorded evidence for this exact version")
    supported, support_reason = _promotion_support(graph, skill, evidence)
    if not supported:
        raise ValueError(f"promotion reliability gate failed: {support_reason}")
    if skill.data.get("status") == "disabled" and evidence.data.get("kind") != "owner_approval":
        raise ValueError("disabled skill restoration requires explicit owner approval")
    if skill.data.get("status") == "promoted":
        return {"ok": True, "changed": False, "skill": skill}
    prior = skill.data.get("status", "candidate")
    promoted = _emit_event(
        graph,
        "skill.promoted",
        {
            "artifact_id": skill.id,
            "skill_id": skill.data["skill_id"],
            "skill_version": skill.data["version"],
            "evidence_id": evidence_id,
            "rationale": rationale,
            "actor": actor,
            "prior_status": prior,
            "reliability_basis": support_reason,
            "is_fixture": skill.data.get("is_fixture", False),
        },
    )
    history = list(skill.data.get("promotion_history") or [])
    history.append(
        {
            "event_id": getattr(promoted, "id", None),
            "evidence_id": evidence_id,
            "rationale": rationale,
            "actor": actor,
            "prior_status": prior,
            "new_status": "promoted",
        }
    )
    graph.patch_object(
        skill.id,
        {"status": "promoted", "demotion_reason": None, "promotion_history": history},
    )
    return {"ok": True, "changed": True, "skill": graph.get_object(skill.id)}


def _set_ineligible(
    graph,
    skill_version_id: str,
    status: str,
    reason: str,
    actor: str,
    evidence_event_id: Optional[str],
) -> dict[str, Any]:
    skill = _find_skill_by_object_or_identity(graph, skill_version_id)
    if skill is None:
        raise ValueError("unknown skill version")
    if status not in {"demoted", "disabled"}:
        raise ValueError("invalid ineligible status")
    event_type = f"skill.{status}"
    if skill.data.get("status") == status:
        return {"ok": True, "changed": False, "skill": skill}
    _emit_event(
        graph,
        event_type,
        {
            "artifact_id": skill.id,
            "skill_id": skill.data["skill_id"],
            "skill_version": skill.data["version"],
            "reason": reason,
            "actor": actor,
            "outcome_evidence_event_id": evidence_event_id,
            "is_fixture": skill.data.get("is_fixture", False),
        },
    )
    metadata = dict(skill.data.get("metadata") or {})
    metadata["demoted_by_reliability"] = bool(evidence_event_id) and status == "demoted"
    graph.patch_object(
        skill.id,
        {"status": status, "demotion_reason": reason, "metadata": metadata},
    )
    return {"ok": True, "changed": True, "skill": graph.get_object(skill.id)}


def demote_skill_fn(
    graph,
    skill_version_id: str,
    reason: str,
    actor: str = "owner",
    evidence_event_id: Optional[str] = None,
) -> dict[str, Any]:
    return _set_ineligible(
        graph, skill_version_id, "demoted", reason, actor, evidence_event_id
    )


def disable_skill_fn(
    graph,
    skill_version_id: str,
    reason: str,
    actor: str = "owner",
    evidence_event_id: Optional[str] = None,
) -> dict[str, Any]:
    return _set_ineligible(
        graph, skill_version_id, "disabled", reason, actor, evidence_event_id
    )


def link_skill_evaluation_fn(
    graph,
    usage_id: str,
    evaluation_id: str,
    *,
    reader=None,
) -> dict[str, Any]:
    """Link at most one Core evaluation to a usage and emit skill.evaluated."""

    read_view = reader or graph
    usage = next(
        (obj for obj in _objects(read_view, "skill_usage") if obj.data.get("usage_id") == usage_id),
        None,
    )
    if usage is None:
        raise ValueError("unknown skill usage")
    evaluation = graph.get_object(evaluation_id)
    if evaluation is None or evaluation.type != "evaluation":
        raise ValueError("evaluation_id must reference a Core evaluation")
    existing = next(
        (
            obj
            for obj in _objects(read_view, "skill_evaluation_link")
            if obj.data.get("usage_id") == usage_id
        ),
        None,
    )
    if existing is not None:
        if existing.data.get("evaluation_id") != evaluation_id:
            raise ValueError("usage_id has already been evaluated")
        return {"ok": True, "created": False, "link": existing}
    evaluated = _emit_event(
        graph,
        "skill.evaluated",
        {
            "usage_id": usage_id,
            "evaluation_id": evaluation_id,
            "skill_id": usage.data["skill_id"],
            "skill_version": usage.data["skill_version"],
            "skill_version_id": usage.data["skill_version_id"],
            "is_fixture": usage.data.get("is_fixture", False),
        },
    )
    link = graph.add_object(
        "skill_evaluation_link",
        SkillEvaluationLink(
            usage_id=usage_id,
            skill_version_id=usage.data["skill_version_id"],
            evaluation_id=evaluation_id,
            evaluated_event_id=getattr(evaluated, "id", "") or "behavior-emitted",
        ).model_dump(),
    )
    graph.add_relation(link.id, usage.id, "evaluation_of_usage")
    graph.add_relation(link.id, evaluation_id, "links_evaluation")
    return {"ok": True, "created": True, "link": link}


def list_eligible_skills_fn(graph) -> list[dict[str, Any]]:
    return [
        {"object_id": obj.id, **obj.data}
        for obj in _objects(graph, "skill")
        if obj.data.get("status") == "promoted"
    ]


@tool(name="author_skill", description="Author an immutable skill version with provenance.", deterministic=True)
def author_skill(
    graph,
    name: str,
    version: str = "0.1.0",
    description: str = "",
    trigger_conditions: Optional[list[str]] = None,
    required_capabilities: Optional[list[str]] = None,
    contraindications: Optional[list[str]] = None,
    source_evidence_refs: Optional[list[str]] = None,
    source_candidate_id: Optional[str] = None,
    actor: str = "owner",
    is_fixture: bool = False,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return author_skill_fn(
        graph,
        name,
        version,
        description,
        trigger_conditions,
        required_capabilities,
        contraindications,
        source_evidence_refs,
        source_candidate_id,
        actor,
        is_fixture,
        metadata,
    )


@tool(name="invoke_skill", description="Invoke one exact eligible skill version idempotently.", deterministic=True)
def invoke_skill(
    graph,
    name: str,
    version: str = "0.1.0",
    usage_id: str = "",
    execution_ref: str = "",
    execution_kind: str = "trial",
    actor: str = "agent",
    source_context: Optional[dict[str, Any]] = None,
    capability_requests: Optional[list[dict[str, Any]]] = None,
    is_fixture: bool = False,
) -> dict[str, Any]:
    return invoke_skill_fn(
        graph,
        name,
        version,
        usage_id,
        execution_ref,
        execution_kind,
        actor,
        source_context,
        capability_requests,
        is_fixture,
    )


@tool(name="record_skill_promotion_evidence", description="Record proof for one exact skill version.", deterministic=True)
def record_skill_promotion_evidence(
    graph,
    skill_version_id: str,
    kind: str = "verification",
    reference_ids: Optional[list[str]] = None,
    rationale: str = "",
    actor: str = "",
    accepted: bool = True,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return record_promotion_evidence_fn(
        graph,
        skill_version_id,
        kind,
        reference_ids or [],
        rationale,
        actor,
        accepted,
        metadata,
    )


@tool(name="promote_skill", description="Promote a skill version from recorded evidence and reliability.", deterministic=True)
def promote_skill(
    graph,
    skill_version_id: str,
    evidence_id: str = "",
    rationale: str = "",
    actor: str = "owner",
) -> dict[str, Any]:
    return promote_skill_fn(graph, skill_version_id, evidence_id, rationale, actor)


@tool(name="demote_skill", description="Make a skill version ineligible without erasing history.", deterministic=True)
def demote_skill(
    graph,
    skill_version_id: str,
    reason: str = "",
    actor: str = "owner",
    evidence_event_id: Optional[str] = None,
) -> dict[str, Any]:
    return demote_skill_fn(graph, skill_version_id, reason, actor, evidence_event_id)


@tool(name="disable_skill", description="Disable a skill version without erasing history.", deterministic=True)
def disable_skill(
    graph,
    skill_version_id: str,
    reason: str = "",
    actor: str = "owner",
    evidence_event_id: Optional[str] = None,
) -> dict[str, Any]:
    return disable_skill_fn(graph, skill_version_id, reason, actor, evidence_event_id)


TOOLS = [
    author_skill,
    invoke_skill,
    record_skill_promotion_evidence,
    promote_skill,
    demote_skill,
    disable_skill,
]


__all__ = [
    "TOOLS",
    "author_skill_fn",
    "invoke_skill_fn",
    "record_promotion_evidence_fn",
    "promote_skill_fn",
    "demote_skill_fn",
    "disable_skill_fn",
    "link_skill_evaluation_fn",
    "list_eligible_skills_fn",
    "_emit_event",
    "_find_skill_by_object_or_identity",
]
