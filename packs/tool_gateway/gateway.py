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

from typing import Any, Literal

from .sanitizer import sanitize_output
from .settings import ToolGatewaySettings


def decide_policy(
    risk_class: str, settings: ToolGatewaySettings
) -> Literal["auto_approve", "hold"]:
    """The gateway's one policy decision: is *risk_class* auto-approvable?

    Everything else about approval (who may resolve a held call, what the
    decision objects look like) builds on this answer.
    """
    return "auto_approve" if risk_class in settings.auto_approve_risk_classes else "hold"


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
        "frame_id": frame_id,
    })

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
        "result_id": result.id,
        "status": new_status,
    }
