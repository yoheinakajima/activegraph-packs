"""Standing-scope tool policies — ADR 0018's automation stage.

The loop this module completes::

    predictions (recorded BEFORE verdicts, product-side)
      → per-scope accuracy (deterministic projection, product-side)
      → sustained accuracy → tool_policy CANDIDATE   [propose_standing_scope_fn]
      → explicit owner approval → PROMOTED           [promote_tool_policy_fn]
      → the gateway's action-class dimension auto-approves the scope's
        R2 capability within the runtime ceiling     [promoted_standing_scope_for]
      → degraded accuracy → DEMOTED, naming the
        missed predictions                           [demote_tool_policy_fn]

Structural guards (each one is a test):

* **R3/R4 can never become standing** — the proposal validates the
  closed R0–R2 set and the ``tool_policy`` schema cannot even represent
  R3/R4; the gateway's R3/R4 branches run before any scope lookup.
* **No backfilled predictions** — every evidence pair's prediction must
  precede its decision in the event log; unresolvable refs fail closed.
* **The predictor reads no score** — nothing in this module (or its
  inputs) touches a score; accuracy is agreement with the owner's
  actual verdicts.
* **Local policy always keeps a scope manual** — a stricter
  ``capability_action_ceilings`` entry wins over any promoted scope
  (the effective ceiling is the minimum).
* **Nothing promotes silently** — only ``promote_tool_policy_fn`` with
  a verified approver emits ``tool_policy.promoted``.
"""

from __future__ import annotations

from typing import Any, Optional


# The versioned earning rule (P6 acceptance: thresholds are explicit,
# versioned constants). Bump rule_version on any semantic change; every
# candidate records the exact rule and threshold snapshot that generated
# it.
STANDING_SCOPE_RULES: dict[str, Any] = {
    "rule_id": "tool_policy.standing_scope.prediction_accuracy",
    "rule_version": 1,
    # Enough decisions that one lucky streak cannot qualify (8 is past
    # the coin-flip regime), while a real weekly-cadence scope can earn
    # it within a dogfood-scale history.
    "min_predictions": 8,
    # 90% with integer math: 9/10 qualifies, 8/10 does not — sustained
    # high-confidence agreement, not a majority vote.
    "min_accuracy_percent": 90,
    # The ceiling of ceilings: standing scopes exist only within R0–R2.
    "max_action_class": "R2",
    # Demotion: any post-promotion accuracy below the earning bar
    # (recomputed over the full pair history) demotes the scope.
    "demote_below_accuracy_percent": 90,
}

_ALLOWED_CLASSES = ("R0", "R1", "R2")

# The verdict-agreement rule, shared with the product's accuracy
# projection: a predicted approve matches an approved decision; a
# predicted reject matches a rejected OR edited decision (an edit is a
# rejection of the proposal as it stood — ADR 0020).
_MATCHES = {
    "approve": {"approved"},
    "reject": {"rejected", "edited"},
}


def _objects(reader, object_type: str) -> list[Any]:
    try:
        return list(reader.objects(type=object_type))
    except Exception:
        return []


def _emit_event(graph, event_type: str, payload: dict[str, Any]):
    """Emit through raw Graph or constrained BehaviorGraph."""

    if not hasattr(graph, "ids"):
        return graph.emit(event_type, payload)
    from activegraph import Event

    event = Event(
        id=graph.ids.event(),
        type=event_type,
        payload=payload,
        actor="tool_gateway",
        timestamp=graph.clock.now(),
    )
    graph.emit(event)
    return event


def _event_index_of_ref(graph, ref: str) -> Optional[int]:
    """The log index a ref resolves to: an event id, or an object's
    ``object.created`` event. None when the ref resolves to nothing."""

    events = list(getattr(graph, "events", []))
    for index, event in enumerate(events):
        if event.id == ref:
            return index
        if event.type == "object.created" and (
            (event.payload or {}).get("id") == ref
        ):
            return index
    return None


def pair_matched(predicted_verdict: str, actual_verdict: str) -> bool:
    """Whether one prediction agreed with the owner's actual verdict."""

    return actual_verdict in _MATCHES.get(predicted_verdict, set())


def accuracy_percent(pairs: list[dict[str, Any]]) -> int:
    """Integer accuracy over pairs — matches * 100 // total (0 if empty)."""

    if not pairs:
        return 0
    matches = sum(
        1
        for pair in pairs
        if pair_matched(
            str(pair.get("predicted_verdict", "")),
            str(pair.get("actual_verdict", "")),
        )
    )
    return matches * 100 // len(pairs)


def _validate_pairs(graph, pairs: list[dict[str, Any]]) -> None:
    """No-backfill guard: every prediction precedes its decision.

    Each pair carries ``prediction_ref`` and ``decided_ref``. Both must
    resolve to log positions and the prediction must come first —
    a prediction recorded after (or untraceable to) its verdict can
    never be automation evidence. Fails closed on unresolvable refs.
    """

    for pair in pairs:
        prediction_ref = str(pair.get("prediction_ref", ""))
        decided_ref = str(pair.get("decided_ref", ""))
        if not prediction_ref or not decided_ref:
            raise ValueError(
                "every prediction pair needs prediction_ref and decided_ref"
            )
        prediction_index = _event_index_of_ref(graph, prediction_ref)
        decided_index = _event_index_of_ref(graph, decided_ref)
        if prediction_index is None or decided_index is None:
            raise ValueError(
                f"prediction pair refs must resolve to recorded events "
                f"(prediction_ref={prediction_ref!r}, "
                f"decided_ref={decided_ref!r}); unresolvable evidence "
                f"fails closed"
            )
        if prediction_index >= decided_index:
            raise ValueError(
                f"prediction {prediction_ref!r} does not precede its "
                f"decision {decided_ref!r} — backfilled predictions can "
                f"never earn automation (ADR 0018)"
            )


def find_tool_policy(graph, scope_key: str):
    """The tool_policy object for *scope_key*, or None."""

    return next(
        (
            obj
            for obj in _objects(graph, "tool_policy")
            if obj.data.get("scope_key") == scope_key
        ),
        None,
    )


def promoted_standing_scope_for(
    graph, capability_key: str, action_class: str
) -> Optional[dict[str, Any]]:
    """The PROMOTED standing scope covering one capability+class, or None.

    This is the gateway's read path: the graph object is the live policy
    state, so a demotion stops auto-approval on the very next decision.
    """

    scope_key = f"{action_class}|{capability_key}"
    policy = find_tool_policy(graph, scope_key)
    if policy is None or policy.data.get("status") != "promoted":
        return None
    evidence = policy.data.get("evidence") or {}
    return {
        "policy_object_id": policy.id,
        "policy_id": policy.data.get("policy_id", ""),
        "policy_version": policy.data.get("policy_version", 1),
        "scope_key": scope_key,
        "prediction_count": evidence.get("prediction_count", 0),
        "accuracy_percent": evidence.get("accuracy_percent", 0),
        "approved_by": policy.data.get("approved_by", ""),
    }


def propose_standing_scope_fn(
    graph,
    *,
    capability_key: str,
    action_class: str,
    prediction_pairs: list[dict[str, Any]],
    proposed_by: str,
    is_fixture: bool = False,
) -> dict[str, Any]:
    """Generate a standing-scope candidate from prediction history.

    Validates the versioned rule: at least ``min_predictions`` resolved
    pairs, accuracy at or above ``min_accuracy_percent``, action class
    within R0–R2, and every prediction recorded before its decision.
    The candidate stores the pairs verbatim as provenance. Emits
    ``tool_policy.proposed``; promotion is a separate, approver-gated
    step. Re-proposing an open candidate is idempotent; re-proposing
    after demotion/disable starts a NEW policy version with the fresh
    evidence.
    """

    rules = STANDING_SCOPE_RULES
    if action_class not in _ALLOWED_CLASSES:
        raise ValueError(
            f"standing scopes exist only within R0-R2; {action_class!r} can "
            f"never become standing regardless of accuracy (ADR 0018)"
        )
    pairs = [dict(pair) for pair in prediction_pairs]
    if len(pairs) < rules["min_predictions"]:
        raise ValueError(
            f"standing scope requires >= {rules['min_predictions']} resolved "
            f"predictions; got {len(pairs)}"
        )
    _validate_pairs(graph, pairs)
    observed_accuracy = accuracy_percent(pairs)
    if observed_accuracy < rules["min_accuracy_percent"]:
        raise ValueError(
            f"standing scope requires accuracy >= "
            f"{rules['min_accuracy_percent']}%; got {observed_accuracy}%"
        )

    scope_key = f"{action_class}|{capability_key}"
    evidence = {
        "prediction_count": len(pairs),
        "accuracy_percent": observed_accuracy,
        "pairs": pairs,
        "thresholds": {
            "rule_id": rules["rule_id"],
            "rule_version": rules["rule_version"],
            "min_predictions": rules["min_predictions"],
            "min_accuracy_percent": rules["min_accuracy_percent"],
        },
    }

    existing = find_tool_policy(graph, scope_key)
    if existing is not None:
        status = existing.data.get("status")
        if status == "candidate":
            return {"ok": True, "created": False, "policy": existing}
        if status == "promoted":
            return {
                "ok": True,
                "created": False,
                "policy": existing,
                "reason": "scope is already promoted",
            }
        # demoted/disabled: fresh evidence starts the next version.
        next_version = int(existing.data.get("policy_version", 1)) + 1
        _emit_event(
            graph,
            "tool_policy.proposed",
            {
                "policy_id": existing.data.get("policy_id", ""),
                "policy_version": next_version,
                "scope_key": scope_key,
                "capability_key": capability_key,
                "action_class": action_class,
                "prediction_count": len(pairs),
                "accuracy_percent": observed_accuracy,
                "rule_id": rules["rule_id"],
                "rule_version": rules["rule_version"],
                "proposed_by": proposed_by,
                "is_fixture": is_fixture,
            },
        )
        graph.patch_object(
            existing.id,
            {
                "status": "candidate",
                "policy_version": next_version,
                "evidence": evidence,
                "proposed_by": proposed_by,
                "approved_by": "",
                "demotion_reason": "",
                "missed_prediction_refs": [],
                "rule_id": rules["rule_id"],
                "rule_version": rules["rule_version"],
            },
        )
        return {"ok": True, "created": True, "policy": graph.get_object(existing.id)}

    policy_id = f"tool_policy_{scope_key}"
    _emit_event(
        graph,
        "tool_policy.proposed",
        {
            "policy_id": policy_id,
            "policy_version": 1,
            "scope_key": scope_key,
            "capability_key": capability_key,
            "action_class": action_class,
            "prediction_count": len(pairs),
            "accuracy_percent": observed_accuracy,
            "rule_id": rules["rule_id"],
            "rule_version": rules["rule_version"],
            "proposed_by": proposed_by,
            "is_fixture": is_fixture,
        },
    )
    policy = graph.add_object(
        "tool_policy",
        {
            "policy_id": policy_id,
            "policy_version": 1,
            "scope_key": scope_key,
            "capability_key": capability_key,
            "action_class": action_class,
            "status": "candidate",
            "rule_id": rules["rule_id"],
            "rule_version": rules["rule_version"],
            "evidence": evidence,
            "proposed_by": proposed_by,
            "is_fixture": is_fixture,
        },
    )
    return {"ok": True, "created": True, "policy": policy}


def promote_tool_policy_fn(
    graph,
    policy_id: str,
    approver_ref: str,
    note: str = "",
    *,
    approver_roles: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Promote a candidate standing scope — the owner's explicit yes.

    The approver is verified exactly like a held capability call
    (Identity/Auth when principals are registered); the thresholds are
    re-validated against the stored evidence at promotion time; the
    ``tool_policy.promoted`` event (keyed ``policy_id`` +
    ``policy_version``) carries the approver and the earning history.
    Nothing else in the gateway can move a policy to promoted.
    """

    from .settings import ToolGatewaySettings
    from .tools import _verify_approver

    if not approver_ref.strip():
        raise ValueError("promotion requires a non-empty approver_ref")
    policy = next(
        (
            obj
            for obj in _objects(graph, "tool_policy")
            if obj.data.get("policy_id") == policy_id
        ),
        None,
    )
    if policy is None:
        raise ValueError(f"unknown tool policy {policy_id!r}")
    if policy.data.get("status") == "promoted":
        return {"ok": True, "changed": False, "policy": policy}
    if policy.data.get("status") == "disabled":
        raise ValueError("a disabled standing scope needs a fresh proposal")

    roles = (
        approver_roles
        if approver_roles is not None
        else ToolGatewaySettings().approver_roles
    )
    verdict = _verify_approver(graph, approver_ref, roles)
    if not verdict["allowed"]:
        raise ValueError(f"approver refused: {verdict['reason']}")

    rules = STANDING_SCOPE_RULES
    evidence = policy.data.get("evidence") or {}
    if int(evidence.get("prediction_count", 0)) < rules["min_predictions"] or (
        int(evidence.get("accuracy_percent", 0)) < rules["min_accuracy_percent"]
    ):
        raise ValueError(
            "stored evidence no longer meets the standing-scope thresholds; "
            "re-propose from current prediction history"
        )

    event = _emit_event(
        graph,
        "tool_policy.promoted",
        {
            "policy_id": policy_id,
            "policy_version": policy.data.get("policy_version", 1),
            "scope_key": policy.data.get("scope_key", ""),
            "capability_key": policy.data.get("capability_key", ""),
            "action_class": policy.data.get("action_class", ""),
            "prediction_count": evidence.get("prediction_count", 0),
            "accuracy_percent": evidence.get("accuracy_percent", 0),
            "approver": approver_ref,
            "approver_verification": verdict["verification"],
            "note": note,
            "rule_id": policy.data.get("rule_id", ""),
            "rule_version": policy.data.get("rule_version", 0),
            "is_fixture": bool(policy.data.get("is_fixture", False)),
        },
    )
    history = list(policy.data.get("promotion_history") or [])
    history.append(
        {
            "event_id": getattr(event, "id", None),
            "policy_version": policy.data.get("policy_version", 1),
            "approver": approver_ref,
            "prior_status": policy.data.get("status", "candidate"),
            "new_status": "promoted",
        }
    )
    graph.patch_object(
        policy.id,
        {
            "status": "promoted",
            "approved_by": approver_ref,
            "promotion_history": history,
            "demotion_reason": "",
            "missed_prediction_refs": [],
        },
    )
    return {
        "ok": True,
        "changed": True,
        "policy": graph.get_object(policy.id),
        "event_id": getattr(event, "id", None),
    }


def demote_tool_policy_fn(
    graph,
    policy_id: str,
    *,
    missed_prediction_refs: list[str],
    observed_accuracy_percent: int,
    actor: str,
    reason: str = "",
    reader=None,
) -> dict[str, Any]:
    """Demote a standing scope on accuracy degradation — reversibly.

    Names the missed predictions (the ADR 0018 requirement: wrong
    predictions are first-class evidence) and the observed accuracy that
    fell below the versioned bar. The gateway's next decision for this
    scope holds for approval again; a later re-proposal with fresh
    evidence starts a new policy version through the same governed path.
    """

    policy = next(
        (
            obj
            for obj in _objects(reader or graph, "tool_policy")
            if obj.data.get("policy_id") == policy_id
        ),
        None,
    )
    if policy is None:
        raise ValueError(f"unknown tool policy {policy_id!r}")
    if policy.data.get("status") != "promoted":
        return {"ok": True, "changed": False, "reason": "not promoted"}
    if not missed_prediction_refs:
        raise ValueError(
            "demotion must name the missed predictions that degraded accuracy"
        )

    event = _emit_event(
        graph,
        "tool_policy.demoted",
        {
            "policy_id": policy_id,
            "policy_version": policy.data.get("policy_version", 1),
            "scope_key": policy.data.get("scope_key", ""),
            "capability_key": policy.data.get("capability_key", ""),
            "action_class": policy.data.get("action_class", ""),
            "missed_prediction_refs": list(missed_prediction_refs),
            "observed_accuracy_percent": observed_accuracy_percent,
            "demote_below_accuracy_percent": STANDING_SCOPE_RULES[
                "demote_below_accuracy_percent"
            ],
            "actor": actor,
            "reason": reason
            or (
                f"accuracy fell to {observed_accuracy_percent}% "
                f"(bar: {STANDING_SCOPE_RULES['demote_below_accuracy_percent']}%)"
            ),
            "is_fixture": bool(policy.data.get("is_fixture", False)),
        },
    )
    graph.patch_object(
        policy.id,
        {
            "status": "demoted",
            "demotion_reason": reason
            or f"accuracy degraded to {observed_accuracy_percent}%",
            "missed_prediction_refs": list(missed_prediction_refs),
        },
    )
    return {
        "ok": True,
        "changed": True,
        "policy": graph.get_object(policy.id),
        "event_id": getattr(event, "id", None),
    }


def disable_tool_policy_fn(
    graph,
    policy_id: str,
    *,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    """Disable a standing scope outright (owner veto, incident response).

    Unlike demotion, a disabled scope cannot be promoted again without a
    completely fresh proposal — the manual override that always wins.
    """

    policy = next(
        (
            obj
            for obj in _objects(graph, "tool_policy")
            if obj.data.get("policy_id") == policy_id
        ),
        None,
    )
    if policy is None:
        raise ValueError(f"unknown tool policy {policy_id!r}")
    if policy.data.get("status") == "disabled":
        return {"ok": True, "changed": False, "policy": policy}
    event = _emit_event(
        graph,
        "tool_policy.disabled",
        {
            "policy_id": policy_id,
            "policy_version": policy.data.get("policy_version", 1),
            "scope_key": policy.data.get("scope_key", ""),
            "actor": actor,
            "reason": reason,
            "is_fixture": bool(policy.data.get("is_fixture", False)),
        },
    )
    graph.patch_object(
        policy.id,
        {"status": "disabled", "demotion_reason": reason},
    )
    return {
        "ok": True,
        "changed": True,
        "policy": graph.get_object(policy.id),
        "event_id": getattr(event, "id", None),
    }
