"""Tool Gateway mechanics shared by the reactive behaviors and the LLM proxies.

Two functions, and exactly two callers each:

  decide_policy          — the single policy decision. Used by the
                           policy_enforcer behavior (reactive path) and by
                           the LLM tool proxies (synchronous path), so there
                           is one policy implementation, not two.

  execute_approved_call  — the single execution implementation: credential
                           injection via Secrets Pack, execution through the
                           local capability registry, output sanitization,
                           CapabilityResult recording, final status patch,
                           produces_result relation. Used by the
                           call_executor behavior and by the LLM tool
                           proxies.

Keeping these here (not in behaviors.py) makes the dependency direction
obvious: behaviors and llm_tools both sit ON TOP of the gateway mechanics;
neither imports the other.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from .sanitizer import sanitize_output
from .settings import ToolGatewaySettings
from .untrusted import scan_for_injection


def decide_policy(
    risk_class: str,
    settings: ToolGatewaySettings,
    *,
    action_class: str = "",
    authority_ceiling: str = "none",
    capability_ceiling: Optional[str] = None,
    standing_scope: Optional[dict[str, Any]] = None,
) -> Literal["auto_approve", "hold"]:
    """The gateway's one policy decision: may this call run unattended?

    Two EXPLICITLY separate dimensions, evaluated independently — neither
    is ever inferred from the other (ADR 0016):

      * **legacy risk dimension** — is *risk_class* in
        ``settings.auto_approve_risk_classes``? Exactly the pre-v0.7
        behavior; hosts that configure only this dimension see decisions
        identical to before.
      * **action-class dimension** — is the canonical *action_class*
        auto-eligible under *authority_ceiling* (the runtime instance
        ceiling, default ``"none"`` = grants nothing) and any stricter
        *capability_ceiling*? R4 always routes to the governance gate,
        R3 always requires approval, missing/invalid class fails closed
        — see the runtime's ``evaluate_action_authority``. **R2 is
        additionally policy-specific** (SCORING_CONTRACT: a ceiling of
        R2 "does not auto-approve every R2 capability"): it grants only
        when *standing_scope* names a PROMOTED tool_policy covering this
        capability (ADR 0018's earned automation). Callers that pass no
        scope get the fail-safe: R2 never auto-grants.

    The call is auto-approved iff EITHER dimension explicitly grants it;
    the action-class dimension can only ADD automation within R0–R2
    under a raised ceiling, never widen the legacy dimension's answer
    for R3/R4 (those are unrepresentable as ceiling grants). Use
    :func:`decide_policy_detail` when the per-dimension reasoning must
    be recorded (approval surfaces do).
    """
    return decide_policy_detail(
        risk_class,
        settings,
        action_class=action_class,
        authority_ceiling=authority_ceiling,
        capability_ceiling=capability_ceiling,
        standing_scope=standing_scope,
    )["decision"]


def decide_policy_detail(
    risk_class: str,
    settings: ToolGatewaySettings,
    *,
    action_class: str = "",
    authority_ceiling: str = "none",
    capability_ceiling: Optional[str] = None,
    standing_scope: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """`decide_policy` with the per-dimension record the surfaces keep.

    Returns::

        {
          "decision": "auto_approve" | "hold",
          "legacy_risk": {"risk_class", "auto_approve"},
          "action_authority": {"action_class", "decision",
                               "matched_policy", "reason", "ceiling",
                               "capability_ceiling", "effective_ceiling",
                               "standing_scope"},
          "granted_by": "legacy_risk" | "action_authority"
                        | "legacy_risk+action_authority" | "",
        }

    ``action_authority`` comes verbatim from the runtime's pure
    ``evaluate_action_authority`` (the same rules the runtime audits),
    so a held call can say WHY the ceiling path declined it: missing
    class, above ceiling, or stricter local policy. One gateway rule
    layers on top (P6, ADR 0018): an ``R2`` grant additionally requires
    *standing_scope* — the promoted tool_policy record from
    ``standing_scopes.promoted_standing_scope_for`` — else the action
    dimension holds with ``r2_requires_promoted_standing_scope``. A
    runtime-side ``authority.decision`` audit event is the caller's job
    when a Runtime handle is available (behaviors have one; see
    behaviors.evaluate_call_policy, which passes the equivalent stricter
    capability ceiling so the audit and this record agree) — this
    function stays graph-free.
    """
    from activegraph.runtime.authority import evaluate_action_authority

    legacy_grants = risk_class in settings.auto_approve_risk_classes
    authority = evaluate_action_authority(
        capability="",
        action_class=action_class,
        ceiling=authority_ceiling,
        capability_ceiling=capability_ceiling,
    )
    action_record = {
        "action_class": authority.action_class,
        "decision": authority.decision,
        "matched_policy": authority.matched_policy,
        "reason": authority.reason,
        "ceiling": authority.ceiling,
        "capability_ceiling": authority.capability_ceiling,
        "effective_ceiling": authority.effective_ceiling,
        "standing_scope": standing_scope,
    }
    # SCORING_CONTRACT: "Level 3 permits a policy to auto-approve a
    # bounded reversible capability; it does not auto-approve every R2
    # capability." The ceiling is the bound; the promoted standing scope
    # is the policy selection. R0/R1 need no scope; R3/R4 never reach
    # here (they hold/gate above).
    if (
        action_class == "R2"
        and action_record["decision"] == "auto_approve"
        and standing_scope is None
    ):
        action_record["decision"] = "require_approval"
        action_record["matched_policy"] = "r2_requires_promoted_standing_scope"
        action_record["reason"] = (
            "R2 is within the ceiling but auto-approval of a bounded "
            "reversible capability requires a PROMOTED standing scope "
            "(tool_policy) covering it; none is promoted for this "
            "capability (ADR 0018)"
        )
    action_grants = action_record["decision"] == "auto_approve"
    granted = [
        name
        for name, grants in (
            ("legacy_risk", legacy_grants),
            ("action_authority", action_grants),
        )
        if grants
    ]
    return {
        "decision": "auto_approve" if granted else "hold",
        "legacy_risk": {
            "risk_class": risk_class,
            "auto_approve": legacy_grants,
        },
        "action_authority": action_record,
        "granted_by": "+".join(granted),
    }


def execute_approved_call(
    graph,
    call_id: str,
    call_data: dict[str, Any],
    settings: ToolGatewaySettings,
    *,
    executed_by: str = "call_executor",
) -> dict[str, Any]:
    """Execute an approved capability call and record everything.

    *call_data* carries the call fields (provider/capability/input/credential
    refs/frame). The credential is resolved at execution time and never
    stored; the output is sanitized before it becomes a CapabilityResult.

    Returns the raw executor result dict plus ``result_id`` — the caller
    decides what (sanitized) subset to surface further.
    """
    from .tools import execute_capability_fn

    provider_name = call_data.get("provider_name", "")
    capability_name = call_data.get("capability_name", "")
    input_data = call_data.get("input_data", {})
    frame_id = call_data.get("frame_id")

    try:
        graph.patch_object(call_id, {"status": "executing"})
    except Exception:
        pass

    # ------------------------------------------------------------------ credential injection
    # Resolve the secret via Secrets Pack NOW — before execution. The value
    # goes into execution_context only; it is NEVER stored in the graph or
    # in any field of CapabilityResult.
    credential_ref_name = call_data.get("credential_ref_name")
    credential_ref_id = call_data.get("credential_ref_id")
    # The gateway is the mediator: capabilities that declare an
    # execution_context parameter receive what the gateway provides — the
    # resolved credential (below) and the graph handle, so graph-writing
    # capabilities (e.g. schedule.create_reminder) need no construction-time
    # closure. Handlers that don't declare it get neither (see
    # execute_capability_fn's TypeError fallback).
    execution_context: dict = {"graph": graph, "call_id": call_id}

    if settings.inject_credentials and credential_ref_name:
        try:
            from packs.secrets.tools import resolve_and_audit_fn

            secret_value = resolve_and_audit_fn(
                graph=graph,
                credential_name=credential_ref_name,
                behavior_name=executed_by,
                frame_id=frame_id,
                call_id=call_id,
                credential_ref_id=credential_ref_id or "",
            )
            if secret_value is not None:
                execution_context["credential"] = secret_value
        except ImportError:
            pass  # Secrets pack not loaded — skip injection silently

    result_data = execute_capability_fn(
        provider_name=provider_name,
        capability_name=capability_name,
        input_data=input_data,
        call_id=call_id,
        frame_id=frame_id,
        execution_context=execution_context,
    )

    raw_output = result_data.get("output_data", "")[: settings.max_output_chars]

    # ------------------------------------------------------------------ output sanitization
    # Always sanitize before storing — prevents credentials or secrets that
    # leak through tool output from being persisted or propagated.
    was_sanitized = False
    if settings.sanitize_output and raw_output:
        raw_output, was_sanitized = sanitize_output(raw_output)

    # ------------------------------------------------------------------ injection scan
    # Tool output is external content; scan it for known injection shapes.
    # A match never blocks the result (heuristics are tripwires, not
    # oracles) — it is recorded on the result and as an injection_flag
    # audit object below, and the LLM-facing envelope carries the warning.
    injection_flags: list[str] = []
    if settings.injection_scan and raw_output:
        injection_flags = scan_for_injection(raw_output)

    stored_output = raw_output if settings.record_output_data else ""

    result = graph.add_object("capability_result", {
        "call_id": call_id,
        "provider_name": provider_name,
        "capability_name": capability_name,
        "output_data": stored_output,
        "error": result_data.get("error"),
        "success": result_data.get("success", True),
        "executed_at": result_data.get("executed_at"),
        "sanitized": was_sanitized,
        "untrusted": True,  # every capability result is external content
        "injection_flags": injection_flags,
        "frame_id": frame_id,
    })

    if injection_flags:
        from datetime import datetime, timezone
        flag = graph.add_object("injection_flag", {
            "call_id": call_id,
            "result_id": result.id,
            "provider_name": provider_name,
            "capability_name": capability_name,
            "patterns": injection_flags,
            "excerpt": raw_output[:300],
            "flagged_at": datetime.now(timezone.utc).isoformat(),
            "frame_id": frame_id,
        })
        # NOTE: add_relation signature is (source, target, type).
        try:
            graph.add_relation(flag.id, result.id, "flags")
        except Exception:
            pass

    new_status = "done" if result_data.get("success") else "failed"
    try:
        graph.patch_object(call_id, {"status": new_status})
    except Exception:
        pass

    # NOTE: add_relation signature is (source, target, type).
    try:
        graph.add_relation(call_id, result.id, "produces_result")
    except Exception:
        pass

    return {
        **result_data,
        "output_data": stored_output,
        "sanitized": was_sanitized,
        "injection_flags": injection_flags,
        "result_id": result.id,
        "status": new_status,
    }
