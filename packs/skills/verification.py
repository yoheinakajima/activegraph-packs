"""Replay verification for promoted skill versions (P6).

The skill half of the ``replay.verified`` emitter (SCORING_CONTRACT key
``(subject_id, subject_version)``). A promoted skill version earns the
claim when its recorded trial re-runs from recorded events inside a
runtime FORK — the fork-trial machinery — and produces the same
governed shape:

1. **Definition integrity** — the definition hash recomputes from the
   stored fields byte-for-byte (the version is the version).
2. **Recorded usage exists** — no recorded trial/real usage means the
   evidence is incomplete; that fails loudly, never silently.
3. **Lineage is replay-complete** — source evidence whose recorded
   acquisition is ``reference_only`` (or ``replay_complete: False``)
   cannot support a replay.verified claim (ADR 0015).
4. **Fork re-run** — the recorded invocation re-executes in
   ``runtime.fork(...)`` with a derived trial usage id; the re-emitted
   ``skill.used`` shape (skill identity, version, declared capabilities)
   must match the recorded one. The fork is discarded; only the
   verification event lands on the real graph.

Emits ``replay.verified`` exactly once per ``(skill_id, version)`` —
re-verification returns the recorded event.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .tools import (
    _canonical_json,
    _emit_event,
    _find_skill_by_object_or_identity,
    invoke_skill_fn,
)


class SkillReplayIncompleteError(RuntimeError):
    """Replay evidence cannot support a replay.verified claim (ADR 0015)."""


def _recorded_usage(graph, skill):
    """The earliest recorded usage of this exact version, or None."""

    usages = [
        obj
        for obj in graph.objects(type="skill_usage")
        if obj.data.get("skill_version_id") == skill.id
    ]
    return usages[0] if usages else None


def _lineage_gaps(graph, skill) -> list[str]:
    """Source evidence refs whose recorded lineage forbids a replay claim."""

    gaps: list[str] = []
    for ref in skill.data.get("source_evidence_refs") or []:
        try:
            evidence = graph.get_object(str(ref))
        except Exception:
            evidence = None
        if evidence is None:
            continue  # opaque provenance ref; nothing recorded to attest
        data = evidence.data or {}
        metadata = data.get("metadata") or {}
        acquisition = (
            data.get("acquisition") or metadata.get("acquisition") or {}
        )
        replay_complete = data.get(
            "replay_complete", metadata.get("replay_complete")
        )
        if replay_complete is False:
            gaps.append(str(ref))
        elif acquisition.get("replay_mode") == "reference_only":
            gaps.append(str(ref))
    return gaps


def verify_skill_replay_fn(
    graph,
    runtime,
    skill_version_id: str,
    *,
    actor: str = "skills",
) -> dict[str, Any]:
    """Earn ``replay.verified`` for one PROMOTED skill version.

    *runtime* is the live Runtime owning *graph* — the fork-trial re-run
    needs it (``runtime.fork``). Raises ``ValueError`` for non-promoted
    versions and :class:`SkillReplayIncompleteError` when the recorded
    evidence cannot support the claim; a failed re-run is an error,
    never a silently skipped check.
    """

    skill = _find_skill_by_object_or_identity(graph, skill_version_id)
    if skill is None:
        raise ValueError("unknown skill version")
    if skill.data.get("status") != "promoted":
        raise ValueError(
            "replay verification applies to promoted skill versions only; "
            f"this version is {skill.data.get('status', 'candidate')!r}"
        )

    # 1. Definition integrity: the locked definition still hashes to the
    #    recorded identity.
    definition = {
        "name": skill.data.get("name", ""),
        "version": skill.data.get("version", ""),
        "description": skill.data.get("description", ""),
        "trigger_conditions": sorted(skill.data.get("trigger_conditions") or []),
        "required_capabilities": sorted(
            skill.data.get("required_capabilities") or []
        ),
        "contraindications": sorted(skill.data.get("contraindications") or []),
    }
    recomputed = hashlib.sha256(
        _canonical_json(definition).encode("utf-8")
    ).hexdigest()
    if recomputed != skill.data.get("definition_hash"):
        raise SkillReplayIncompleteError(
            f"skill {skill.id!r} definition no longer hashes to its recorded "
            f"identity — replay check failed, not skipped"
        )

    # 2. A recorded usage is the replay input.
    usage = _recorded_usage(graph, skill)
    if usage is None:
        raise SkillReplayIncompleteError(
            f"skill {skill.id!r} has no recorded usage; a replay.verified "
            f"claim needs a recorded trial to re-run"
        )

    # 3. ADR 0015: reference_only lineage cannot support the claim.
    gaps = _lineage_gaps(graph, skill)
    if gaps:
        raise SkillReplayIncompleteError(
            f"skill {skill.id!r} has reference_only / replay-incomplete "
            f"source lineage {gaps}; ADR 0015: such lineage may not support "
            f"a replay.verified claim"
        )

    # 4. Fork-trial re-run from recorded events. The fork inherits the
    #    full log (definition, provider objects, prior usage); the
    #    re-invocation must reproduce the recorded governed shape.
    anchor = usage.data.get("used_event_id") or ""
    fork_at = anchor if anchor and anchor != "behavior-emitted" else (
        graph.events[-1].id if getattr(graph, "events", None) else ""
    )
    fork = runtime.fork(at_event=fork_at, behaviors=[])
    replay_usage_id = f"replay::{usage.data.get('usage_id')}"
    result = invoke_skill_fn(
        fork.graph,
        skill.data.get("name", ""),
        skill.data.get("version", ""),
        usage_id=replay_usage_id,
        execution_ref=f"replay::{usage.data.get('execution_ref')}",
        execution_kind="trial",
        actor=actor,
        source_context={"replay_of": usage.data.get("usage_id")},
        is_fixture=bool(usage.data.get("is_fixture", False)),
    )
    replayed = result["usage"]
    stable_fields = ("skill_id", "skill_version", "required_capabilities")
    mismatches = {
        field: {
            "recorded": usage.data.get(field),
            "replayed": replayed.data.get(field),
        }
        for field in stable_fields
        if usage.data.get(field) != replayed.data.get(field)
    }
    if mismatches:
        raise SkillReplayIncompleteError(
            f"fork re-run of skill {skill.id!r} diverged from the recorded "
            f"usage: {mismatches}"
        )

    subject_id = skill.data.get("skill_id", "")
    subject_version = skill.data.get("version", "")
    prior = next(
        (
            e
            for e in getattr(graph, "events", [])
            if e.type == "replay.verified"
            and (e.payload or {}).get("subject_id") == subject_id
            and (e.payload or {}).get("subject_version") == subject_version
        ),
        None,
    )
    if prior is not None:
        return {"ok": True, "created": False, "event_id": prior.id}

    event = _emit_event(
        graph,
        "replay.verified",
        {
            # SCORING_CONTRACT identity:
            "subject_id": subject_id,
            "subject_version": subject_version,
            "subject_type": "skill_version",
            "subject_object_id": skill.id,
            "method": "fork_trial_rerun",
            "recorded_usage_id": usage.data.get("usage_id"),
            "replay_usage_id": replay_usage_id,
            "fork_run_id": getattr(getattr(fork, "graph", None), "run_id", ""),
            "checks": [
                {"check": "definition_hash", "passed": True},
                {"check": "lineage_replay_complete", "passed": True},
                {"check": "fork_trial_rerun", "passed": True},
            ],
            "actor": actor,
            "is_fixture": bool(skill.data.get("is_fixture", False)),
        },
    )
    return {"ok": True, "created": True, "event_id": getattr(event, "id", None)}
