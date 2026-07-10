"""Tool Gateway Pack behaviors — v0.1.

Four behaviors covering the full capability call lifecycle:

1. call_recorder — on capability_call.created, creates a calls relation
   to the provider and logs the call.

2. policy_enforcer — on capability_call.created (proposed), checks
   risk_class against auto_approve settings. For auto-approved calls,
   creates a CapabilityApproval object (the graph-visible trigger for
   call_executor). Non-auto-approved calls get status='policy_checking'.

3. call_executor — on capability_approval.created, executes the approved
   call. If credential_ref_name + credential_ref_id are present, resolves
   and injects the credential via Secrets Pack before execution (the secret
   is discarded immediately after use; never stored). Sanitizes output via
   the built-in sanitizer before creating CapabilityResult.

4. result_sourcer — on capability_result.created, maps the sanitized result
   to a Core source object so downstream behaviors can observe output.

Design rules:
- @behavior signature: (name, on, where, view, creates, budget, priority)
- 'description' goes in the docstring, not @behavior
- Behaviors must not store actual secrets — credential_ref_name/id only
- Policy decisions must be graph-visible (CapabilityApproval objects)
- call_executor is triggered by capability_approval, not by capability_call
  directly — this avoids any race condition with policy_enforcer
- graph.objects() is unsafe in behaviors — use get_object(id) instead
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from activegraph.packs import behavior

from .gateway import decide_policy_detail, execute_approved_call
from .object_types import CapabilityApproval
from .settings import ToolGatewaySettings


def evaluate_call_policy(
    *,
    capability_key: str,
    risk_class: str,
    action_class: str,
    settings: ToolGatewaySettings,
    runtime=None,
    caused_by=None,
    actor: str = "policy_enforcer",
) -> dict:
    """Both policy dimensions for one call, runtime-audited when possible.

    The legacy risk dimension and the action-class dimension are
    evaluated independently (see gateway.decide_policy_detail — no
    cross-inference). The instance authority ceiling comes from the
    Runtime when one is available; a call WITH a declared action_class
    is additionally evaluated through
    ``Runtime.evaluate_capability_authority`` so the runtime emits its
    ``authority.decision`` audit event (CONTRACT v1.9 #3). Calls without
    an action_class never touch the runtime authority path — their event
    log stays exactly as before v0.7.
    """
    capability_ceiling = settings.capability_action_ceilings.get(capability_key)
    ceiling = "none"
    if runtime is not None and hasattr(runtime, "authority_ceiling"):
        ceiling = runtime.authority_ceiling()
    detail = decide_policy_detail(
        risk_class,
        settings,
        action_class=action_class,
        authority_ceiling=ceiling,
        capability_ceiling=capability_ceiling,
    )
    detail["action_authority"]["capability"] = capability_key
    if (
        action_class
        and runtime is not None
        and hasattr(runtime, "evaluate_capability_authority")
    ):
        audited = runtime.evaluate_capability_authority(
            capability=capability_key,
            action_class=action_class,
            capability_ceiling=capability_ceiling,
            actor=actor,
            caused_by=caused_by,
        )
        detail["action_authority"]["audit_event_id"] = audited.event_id
    return detail


# ------------------------------------------------------------------ behaviors


@behavior(
    name="call_recorder",
    on=["object.created"],
    where={"object.type": "capability_call"},
    creates=[],
)
def call_recorder(event, graph, ctx, *, settings: ToolGatewaySettings):
    """Record a proposed capability call and create its provider relation.

    On: object.created (capability_call)
    Creates: calls(capability_call → capability_provider) relation
    Side effects: logs call for audit trail

    Runs immediately when a capability_call is added to the graph.
    Does not execute the call — that is handled by call_executor after
    policy_enforcer creates a CapabilityApproval.
    """
    obj = event.payload.get("object", {})
    call_id = obj.get("id")
    call_data = obj.get("data", {})

    provider_id = call_data.get("provider_id")
    if not provider_id or not call_id:
        return

    # Create 'calls' relation: capability_call → capability_provider.
    # NOTE: add_relation signature is (source, target, type).
    try:
        graph.add_relation(call_id, provider_id, "calls")
    except Exception:
        pass


@behavior(
    name="policy_enforcer",
    on=["object.created"],
    where={"object.type": "capability_call"},
    creates=["capability_approval"],
)
def policy_enforcer(event, graph, ctx, *, settings: ToolGatewaySettings):
    """Check a proposed capability call against the policy configuration.

    On: object.created (capability_call, status=proposed)
    Creates: capability_approval (if auto-approved) — serves as the trigger
             for call_executor behavior
    Side effects: patches capability_call.status to 'approved' or 'policy_checking'

    Two independent dimensions decide (gateway.decide_policy_detail):
    the legacy risk dimension (risk_class in
    settings.auto_approve_risk_classes — exactly the pre-v0.7 behavior)
    and the action-class dimension (canonical action_class vs the
    runtime instance ceiling and any stricter per-capability ceiling;
    R3/R4 and missing classes never auto-approve here). Auto-approved
    calls get a CapabilityApproval which triggers call_executor; held
    calls go to status='policy_checking' carrying the per-dimension
    reasons in metadata so the approvals surface can say WHY.
    """
    obj = event.payload.get("object", {})
    call_id = obj.get("id")
    call_data = obj.get("data", {})

    risk_class = call_data.get("risk_class", "medium")
    action_class = call_data.get("action_class", "")
    current_status = call_data.get("status", "proposed")

    if current_status != "proposed":
        return

    now = datetime.now(timezone.utc).isoformat()

    capability_key = (
        f"{call_data.get('provider_name', '')}."
        f"{call_data.get('capability_name', '')}"
    )
    policy = evaluate_call_policy(
        capability_key=capability_key,
        risk_class=risk_class,
        action_class=action_class,
        settings=settings,
        runtime=getattr(ctx, "_runtime", None),
        caused_by=getattr(event, "id", None),
    )

    if policy["decision"] == "auto_approve":
        # Patch the call status
        try:
            graph.patch_object(call_id, {"status": "approved"})
        except Exception:
            pass

        # Create CapabilityApproval — this is the trigger for call_executor.
        # Copy credential_ref_id from the call so call_executor can inject creds.
        approval = graph.add_object(
            "capability_approval",
            CapabilityApproval(
                call_id=call_id,
                provider_id=call_data.get("provider_id", ""),
                provider_name=call_data.get("provider_name", ""),
                capability_name=call_data.get("capability_name", ""),
                action_class=action_class,
                input_data=call_data.get("input_data", {}),
                credential_ref_name=call_data.get("credential_ref_name"),
                credential_ref_id=call_data.get("credential_ref_id"),
                frame_id=call_data.get("frame_id"),
                policy_decision="auto_approved",
                approver="policy_enforcer",
                approved_at=now,
                metadata={
                    "risk_class": risk_class,
                    # Which dimension(s) granted, and the action-class
                    # reasoning — the two dimensions stay separately
                    # named all the way into the audit object.
                    **(
                        {
                            "granted_by": policy["granted_by"],
                            "action_authority": policy["action_authority"],
                        }
                        if action_class
                        else {}
                    ),
                },
            ).model_dump(),
        )

        # Create approved_by relation: capability_call → capability_approval.
        # NOTE: add_relation signature is (source, target, type).
        try:
            graph.add_relation(call_id, approval.id, "approved_by")
        except Exception:
            pass
    else:
        try:
            graph.patch_object(call_id, {"status": "policy_checking"})
        except Exception:
            pass
        # Record WHY the action-class path declined (missing class /
        # above ceiling / stricter policy / R3 / R4) on the call, so
        # pending_approvals can display it. Only when the new field is
        # in play — calls without an action_class keep their exact
        # legacy event shape.
        if action_class:
            try:
                merged = dict(call_data.get("metadata", {}) or {})
                merged["action_authority"] = policy["action_authority"]
                merged["granted_by"] = policy["granted_by"]
                graph.patch_object(call_id, {"metadata": merged})
            except Exception:
                pass


@behavior(
    name="call_executor",
    on=["object.created"],
    where={"object.type": "capability_approval"},
    creates=["capability_result"],
)
def call_executor(event, graph, ctx, *, settings: ToolGatewaySettings):
    """Execute an approved capability call and create a CapabilityResult.

    On: object.created (capability_approval)
    Creates: capability_result
    Creates: produces_result(capability_call → capability_result) relation
    Side effects: patches capability_call.status to 'done' or 'failed'

    This behavior is the only place where actual capability execution happens.
    It fires ONLY when a CapabilityApproval exists — ensuring that every
    executed call was explicitly approved by policy_enforcer.

    Delegates to gateway.execute_approved_call — the single execution
    implementation shared with the LLM tool proxies (llm_tools.py).
    """
    obj = event.payload.get("object", {})
    approval_data = obj.get("data", {})

    call_id = approval_data.get("call_id", "")
    capability_name = approval_data.get("capability_name", "")

    if not call_id or not capability_name:
        return

    # Idempotency guard: execute only calls sitting at 'approved'. An LLM
    # tool proxy executes its call inline and records the approval afterward
    # (the call is already 'done' when this fires); replayed approvals hit
    # the same guard. One approval → at most one execution.
    try:
        call = graph.get_object(call_id)
    except Exception:
        call = None
    if call is None or call.data.get("status") != "approved":
        return

    execute_approved_call(
        graph,
        call_id,
        approval_data,
        settings,
        executed_by="call_executor",
    )


@behavior(
    name="result_sourcer",
    on=["object.created"],
    where={"object.type": "capability_result"},
    creates=["source"],
)
def result_sourcer(event, graph, ctx, *, settings: ToolGatewaySettings):
    """Map a capability result to a Core source object.

    On: object.created (capability_result)
    Creates: source (kind=tool_result) with the result content
    Creates: sourced_as(capability_result → source) relation

    This is the bridge between Tool Gateway and Core Pack.
    By creating a source object, downstream Core behaviors
    (observation_extractor etc.) can observe the tool output.
    """
    if not settings.create_source_from_result:
        return

    obj = event.payload.get("object", {})
    result_id = obj.get("id")
    result_data = obj.get("data", {})

    output_data = result_data.get("output_data", "")
    if not output_data:
        return

    provider_name = result_data.get("provider_name", "unknown")
    capability_name = result_data.get("capability_name", "unknown")
    frame_id = result_data.get("frame_id")
    call_id = result_data.get("call_id", "")

    content = output_data[: settings.max_output_chars]

    # Create Core source object from the result
    source = graph.add_object(
        "source",
        {
            "kind": "tool_result",
            "content": content,
            "url": None,
            "channel": "tool_gateway",
            "sender_ref": f"{provider_name}.{capability_name}",
            "frame_id": frame_id,
            "metadata": {
                "call_id": call_id,
                "provider": provider_name,
                "capability": capability_name,
            },
        },
    )

    # Update result with source_id
    try:
        graph.patch_object(result_id, {"source_id": source.id})
    except Exception:
        pass

    # Create sourced_as relation: capability_result → source.
    # NOTE: add_relation signature is (source, target, type).
    try:
        graph.add_relation(result_id, source.id, "sourced_as")
    except Exception:
        pass


BEHAVIORS = [call_recorder, policy_enforcer, call_executor, result_sourcer]
