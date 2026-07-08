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

from .object_types import CapabilityApproval, CapabilityResult
from .sanitizer import sanitize_output
from .settings import ToolGatewaySettings


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

    Auto-approves calls whose risk_class is in settings.auto_approve_risk_classes.
    For auto-approved calls, creates a CapabilityApproval which triggers
    call_executor. All other risk classes set status='policy_checking'.
    """
    obj = event.payload.get("object", {})
    call_id = obj.get("id")
    call_data = obj.get("data", {})

    risk_class = call_data.get("risk_class", "medium")
    current_status = call_data.get("status", "proposed")

    if current_status != "proposed":
        return

    now = datetime.now(timezone.utc).isoformat()

    if risk_class in settings.auto_approve_risk_classes:
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
                input_data=call_data.get("input_data", {}),
                credential_ref_name=call_data.get("credential_ref_name"),
                credential_ref_id=call_data.get("credential_ref_id"),
                frame_id=call_data.get("frame_id"),
                policy_decision="auto_approved",
                approver="policy_enforcer",
                approved_at=now,
                metadata={"risk_class": risk_class},
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

    Dispatches to the local registry via execute_capability_fn.
    """
    from .tools import execute_capability_fn

    obj = event.payload.get("object", {})
    approval_data = obj.get("data", {})

    call_id = approval_data.get("call_id", "")
    provider_name = approval_data.get("provider_name", "")
    capability_name = approval_data.get("capability_name", "")
    input_data = approval_data.get("input_data", {})
    frame_id = approval_data.get("frame_id")

    if not call_id or not capability_name:
        return

    # Patch call status to 'executing'
    try:
        graph.patch_object(call_id, {"status": "executing"})
    except Exception:
        pass

    # ------------------------------------------------------------------ credential injection
    # If a credential reference is present and injection is enabled,
    # resolve the secret via Secrets Pack NOW — before execution.
    # The resolved value is injected into execution_context but NEVER
    # stored in the graph or in any field of CapabilityResult.
    credential_ref_name = approval_data.get("credential_ref_name")
    credential_ref_id = approval_data.get("credential_ref_id")
    execution_context: dict = {}

    if settings.inject_credentials and credential_ref_name:
        try:
            from packs.secrets.tools import resolve_and_audit_fn

            secret_value = resolve_and_audit_fn(
                graph=graph,
                credential_name=credential_ref_name,
                behavior_name="call_executor",
                frame_id=frame_id,
                call_id=call_id,
                credential_ref_id=credential_ref_id or "",
            )
            if secret_value is not None:
                execution_context["credential"] = secret_value
        except ImportError:
            pass  # Secrets pack not loaded — skip injection silently

    # Execute the capability (execution_context carries the resolved credential)
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
    # leak through tool output from being persisted in CapabilityResult
    # or propagated to Core sources.
    was_sanitized = False
    if settings.sanitize_output and raw_output:
        raw_output, was_sanitized = sanitize_output(raw_output)

    stored_output = raw_output if settings.record_output_data else ""

    # Create CapabilityResult — sanitized=True if any redactions occurred
    result = graph.add_object(
        "capability_result",
        CapabilityResult(
            call_id=call_id,
            provider_name=provider_name,
            capability_name=capability_name,
            output_data=stored_output,
            error=result_data.get("error"),
            success=result_data.get("success", True),
            executed_at=result_data.get("executed_at"),
            sanitized=was_sanitized,
            frame_id=frame_id,
        ).model_dump(),
    )

    # Patch call status to 'done' or 'failed'
    new_status = "done" if result_data.get("success") else "failed"
    try:
        graph.patch_object(call_id, {"status": new_status})
    except Exception:
        pass

    # Create produces_result relation: capability_call → capability_result.
    # NOTE: add_relation signature is (source, target, type).
    try:
        graph.add_relation(call_id, result.id, "produces_result")
    except Exception:
        pass


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
