"""Tool Gateway Pack tools — v0.1.

Provides execute_capability: the main tool for dispatching a capability
call after policy approval. Returns a CapabilityResult dict.

The `@tool`-decorated object is registered in the pack for the runtime.
The raw function is exported as `execute_capability_fn` so Python code
can call it directly without going through the Tool wrapper.

In production, behaviors should NOT call execute_capability directly —
they should create a CapabilityCall object (proposed), let policy_enforcer
approve it, and then call execute_capability. This keeps all calls
graph-visible and policy-gated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from activegraph.packs import tool


# ------------------------------------------------------------------ registry


@dataclass(frozen=True)
class CapabilitySpec:
    """A registered local capability: the handler plus the metadata that
    lets the gateway expose it safely — as a policy-checked call and,
    via llm_tools.as_llm_tool, as an LLM-callable tool.

    input_schema (a Pydantic model) is what the LLM sees as the tool's
    parameter schema; without it a capability can still be executed
    graph-side but cannot be offered to a model (an empty schema would
    make the model call it with {}).
    """

    provider_name: str
    capability_name: str
    fn: Callable
    input_schema: Optional[type] = None
    description: str = ""
    risk_class: str = "low"
    credential_ref_name: Optional[str] = None

    @property
    def key(self) -> str:
        return f"{self.provider_name}.{self.capability_name}"


# Registered local capabilities: "{provider_name}.{capability_name}" -> CapabilitySpec
_LOCAL_REGISTRY: dict[str, CapabilitySpec] = {}


def register_local_capability(
    provider_name: str,
    capability_name: str,
    fn: Callable,
    *,
    input_schema: Optional[type] = None,
    description: str = "",
    risk_class: str = "low",
    credential_ref_name: Optional[str] = None,
) -> CapabilitySpec:
    """Register a local Python function as a capability.

    The key is "{provider_name}.{capability_name}" (case-sensitive).
    The keyword metadata is what makes the capability LLM-exposable via
    llm_tools_for / as_llm_tool — see llm_tools.py.

    Example:
        class LookupInput(BaseModel):
            company_name: str

        def my_lookup(company_name: str) -> dict:
            return {"company": company_name, "founded": 2021}

        register_local_capability(
            "crm", "lookup_company", my_lookup,
            input_schema=LookupInput,
            description="Look up a company in the CRM.",
            risk_class="low",
        )
    """
    spec = CapabilitySpec(
        provider_name=provider_name,
        capability_name=capability_name,
        fn=fn,
        input_schema=input_schema,
        description=description,
        risk_class=risk_class,
        credential_ref_name=credential_ref_name,
    )
    _LOCAL_REGISTRY[spec.key] = spec
    return spec


def get_capability_spec(key: str) -> Optional[CapabilitySpec]:
    """Look up a registered capability by its "provider.capability" key."""
    return _LOCAL_REGISTRY.get(key)


def clear_local_registry() -> None:
    """Empty the local capability registry — call between test fixtures
    (mirrors clear_session_registry / clear_thread_registry elsewhere)."""
    _LOCAL_REGISTRY.clear()


def registered_capability_keys() -> list[str]:
    """All registered capability keys, in registration order."""
    return list(_LOCAL_REGISTRY.keys())


# ------------------------------------------------------------------ raw function (callable directly)


def execute_capability_fn(
    provider_name: str,
    capability_name: str,
    input_data: dict[str, Any],
    call_id: str = "",
    frame_id: Optional[str] = None,
    execution_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Dispatch a capability call to the appropriate handler.

    Routes to:
    1. Local registry (registered Python functions)
    2. Falls back to a mock response for testing

    Real API/MCP/SDK adapters will be added in v0.2.

    Args:
        provider_name: Name of the capability provider
        capability_name: Name of the capability to invoke
        input_data: Input parameters (no secrets)
        call_id: ID of the CapabilityCall being executed
        frame_id: Optional frame scope
        execution_context: Runtime context injected by call_executor.
            May contain a resolved 'credential' value for authenticated calls.
            The credential is used here and NEVER persisted or returned.

    Returns:
        Dict with: output_data (str), error (str|None),
                   success (bool), executed_at (str),
                   credential_injected (bool)
    """
    now = datetime.now(timezone.utc).isoformat()
    key = f"{provider_name}.{capability_name}"
    ctx = execution_context or {}
    credential_injected = "credential" in ctx  # True if Secrets Pack injected a value

    try:
        if key in _LOCAL_REGISTRY:
            # Registered handlers receive input_data kwargs.
            # They may also receive execution_context if they accept it,
            # but most handlers are simple and do not need it.
            handler = _LOCAL_REGISTRY[key].fn
            try:
                result = handler(**input_data, execution_context=ctx)
            except TypeError:
                result = handler(**input_data)
            output_str = json.dumps(result) if not isinstance(result, str) else result
        else:
            # No handler registered — return a structured mock for testing
            output_str = json.dumps({
                "mock": True,
                "provider": provider_name,
                "capability": capability_name,
                "inputs": input_data,
                "note": f"No handler registered for '{key}'. Register via register_local_capability().",
            })

        return {
            "output_data": output_str,
            "error": None,
            "success": True,
            "executed_at": now,
            "credential_injected": credential_injected,
        }

    except Exception as exc:
        return {
            "output_data": "",
            "error": f"{type(exc).__name__}: {exc}",
            "success": False,
            "executed_at": now,
            "credential_injected": credential_injected,
        }


# ------------------------------------------------------------------ approval resolution
#
# A capability_call that policy_enforcer holds at status='policy_checking' is
# resumed (or refused) HERE — and only here. Both functions write ordinary
# graph objects, so the graph stays the single source of truth for approval
# state: approve creates the same capability_approval object policy_enforcer
# creates for auto-approved calls (call_executor fires on it — one execution
# trigger, no second path), and deny creates a capability_denial so refusals
# carry the same audit weight as grants.


def _verify_approver(graph, approver_ref: str, approver_roles: list[str]) -> dict:
    """Check that *approver_ref* may resolve held calls.

    Identity/Auth is an optional composition partner (guarded import). The
    rule is: verification happens when verification is possible.

      - identity_auth absent, or loaded with no principals registered →
        allow, marked 'identity_unverified' (the demo / packs-only case).
      - principals registered and approver resolves to one → require a role
        in *approver_roles*.
      - principals registered but approver is unknown → refuse. Once an
        identity system is active, anonymous approvals would be a bypass.

    Returns {"allowed": bool, "verification": str, "role": str|None,
             "reason": str}.
    """
    try:
        from packs.identity_auth.tools import lookup_principal_fn
    except Exception:
        return {
            "allowed": True,
            "verification": "identity_unverified",
            "role": None,
            "reason": "identity_auth not installed",
        }

    try:
        any_principals = any(True for _ in graph.objects(type="principal"))
    except Exception:
        # principal type not registered → identity_auth pack not loaded.
        return {
            "allowed": True,
            "verification": "identity_unverified",
            "role": None,
            "reason": "identity_auth not loaded",
        }

    if not any_principals:
        return {
            "allowed": True,
            "verification": "identity_unverified",
            "role": None,
            "reason": "no principals registered",
        }

    principal = lookup_principal_fn(graph, approver_ref)
    if principal is None:
        return {
            "allowed": False,
            "verification": "identity_verified",
            "role": None,
            "reason": f"approver {approver_ref!r} does not resolve to a principal",
        }

    role = principal.get("role", "unknown")
    if role in approver_roles:
        return {
            "allowed": True,
            "verification": "identity_verified",
            "role": role,
            "reason": "role_allows",
        }
    return {
        "allowed": False,
        "verification": "identity_verified",
        "role": role,
        "reason": f"role {role!r} not in approver_roles {approver_roles}",
    }


def pending_approvals_fn(graph) -> list[dict[str, Any]]:
    """List capability calls held for manual approval.

    The graph is the single source of truth for approval state: a pending
    approval IS a capability_call at status='policy_checking'. Returns
    display-ready dicts sorted oldest-first (approval queues are FIFO).
    """
    pending = []
    try:
        for obj in graph.objects(type="capability_call"):
            if obj.data.get("status") != "policy_checking":
                continue
            pending.append({
                "call_id": obj.id,
                "provider_name": obj.data.get("provider_name", ""),
                "capability_name": obj.data.get("capability_name", ""),
                "risk_class": obj.data.get("risk_class", "medium"),
                "input_data": obj.data.get("input_data", {}),
                "proposed_by": obj.data.get("proposed_by"),
                "proposed_at": obj.data.get("proposed_at"),
                "frame_id": obj.data.get("frame_id"),
            })
    except Exception:
        pass
    pending.sort(key=lambda p: p.get("proposed_at") or "")
    return pending


def approve_capability_fn(
    graph,
    call_id: str,
    approver_ref: str,
    note: str = "",
    *,
    approver_roles: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Approve a held capability call so call_executor runs it.

    Validates the call is at status='policy_checking', verifies the
    approver (see _verify_approver), then creates the capability_approval
    object — the same graph-visible execution trigger policy_enforcer
    creates for auto-approved calls. call_executor picks it up on the next
    settle; this function never executes anything itself.

    Returns {"ok": bool, "approval_id": str|None, "reason": str}.
    """
    from .settings import ToolGatewaySettings

    roles = approver_roles if approver_roles is not None else ToolGatewaySettings().approver_roles
    now = datetime.now(timezone.utc).isoformat()

    try:
        call = graph.get_object(call_id)
    except Exception:
        call = None
    if call is None or call.type != "capability_call":
        return {"ok": False, "approval_id": None, "reason": f"no capability_call {call_id!r}"}

    status = call.data.get("status")
    if status != "policy_checking":
        return {
            "ok": False,
            "approval_id": None,
            "reason": f"call is {status!r}, not 'policy_checking' — only held calls can be approved",
        }

    verdict = _verify_approver(graph, approver_ref, roles)
    if not verdict["allowed"]:
        return {"ok": False, "approval_id": None, "reason": verdict["reason"]}

    graph.patch_object(call_id, {"status": "approved"})
    approval = graph.add_object("capability_approval", {
        "call_id": call_id,
        "provider_id": call.data.get("provider_id", ""),
        "provider_name": call.data.get("provider_name", ""),
        "capability_name": call.data.get("capability_name", ""),
        "input_data": call.data.get("input_data", {}),
        "credential_ref_name": call.data.get("credential_ref_name"),
        "credential_ref_id": call.data.get("credential_ref_id"),
        "frame_id": call.data.get("frame_id"),
        "policy_decision": "manual",
        "approver": approver_ref,
        "approved_at": now,
        "metadata": {
            "risk_class": call.data.get("risk_class", "medium"),
            "note": note,
            "verification": verdict["verification"],
            "approver_role": verdict["role"],
        },
    })
    # NOTE: add_relation signature is (source, target, type).
    try:
        graph.add_relation(call_id, approval.id, "approved_by")
    except Exception:
        pass
    return {"ok": True, "approval_id": approval.id, "reason": "approved"}


def deny_capability_fn(
    graph,
    call_id: str,
    approver_ref: str,
    reason: str = "",
    *,
    approver_roles: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Deny a held capability call — an audited refusal, not a status flip.

    Validates and verifies exactly like approve_capability_fn, then patches
    the call to status='rejected' and records a capability_denial object
    (who refused, why, when) linked via denied_by.

    Returns {"ok": bool, "denial_id": str|None, "reason": str}.
    """
    from .settings import ToolGatewaySettings

    roles = approver_roles if approver_roles is not None else ToolGatewaySettings().approver_roles
    now = datetime.now(timezone.utc).isoformat()

    try:
        call = graph.get_object(call_id)
    except Exception:
        call = None
    if call is None or call.type != "capability_call":
        return {"ok": False, "denial_id": None, "reason": f"no capability_call {call_id!r}"}

    status = call.data.get("status")
    if status != "policy_checking":
        return {
            "ok": False,
            "denial_id": None,
            "reason": f"call is {status!r}, not 'policy_checking' — only held calls can be denied",
        }

    verdict = _verify_approver(graph, approver_ref, roles)
    if not verdict["allowed"]:
        return {"ok": False, "denial_id": None, "reason": verdict["reason"]}

    graph.patch_object(call_id, {"status": "rejected"})
    denial = graph.add_object("capability_denial", {
        "call_id": call_id,
        "provider_name": call.data.get("provider_name", ""),
        "capability_name": call.data.get("capability_name", ""),
        "denier": approver_ref,
        "reason": reason,
        "denied_at": now,
        "frame_id": call.data.get("frame_id"),
        "metadata": {
            "risk_class": call.data.get("risk_class", "medium"),
            "verification": verdict["verification"],
            "denier_role": verdict["role"],
        },
    })
    # NOTE: add_relation signature is (source, target, type).
    try:
        graph.add_relation(call_id, denial.id, "denied_by")
    except Exception:
        pass
    return {"ok": True, "denial_id": denial.id, "reason": "denied"}


# ------------------------------------------------------------------ tool wrapper (for pack registration)


@tool(
    name="execute_capability",
    description=(
        "Execute an approved capability call and return the result. "
        "Call this ONLY after policy_enforcer has set status='approved'. "
        "Returns a dict with: output_data, error, success, executed_at."
    ),
)
def execute_capability(
    provider_name: str,
    capability_name: str,
    input_data: dict[str, Any],
    call_id: str = "",
    frame_id: Optional[str] = None,
) -> dict[str, Any]:
    """Registered tool wrapper — delegates to execute_capability_fn."""
    return execute_capability_fn(
        provider_name=provider_name,
        capability_name=capability_name,
        input_data=input_data,
        call_id=call_id,
        frame_id=frame_id,
    )


@tool(
    name="pending_approvals",
    description=(
        "List capability calls held for manual approval (status='policy_checking'). "
        "The graph is the single source of truth for approval state. "
        "Returns display-ready dicts, oldest first."
    ),
)
def pending_approvals(graph) -> list[dict[str, Any]]:
    """Registered tool wrapper — delegates to pending_approvals_fn."""
    return pending_approvals_fn(graph)


@tool(
    name="approve_capability",
    description=(
        "Approve a capability call held at status='policy_checking'. Creates the "
        "capability_approval object that triggers call_executor — the same trigger "
        "auto-approval uses, so there is exactly one execution path. The approver "
        "is verified against Identity/Auth when principals are registered."
    ),
)
def approve_capability(graph, call_id: str, approver_ref: str, note: str = "") -> dict[str, Any]:
    """Registered tool wrapper — delegates to approve_capability_fn."""
    return approve_capability_fn(graph, call_id, approver_ref, note)


@tool(
    name="deny_capability",
    description=(
        "Deny a capability call held at status='policy_checking'. Patches the call "
        "to 'rejected' and records a capability_denial object (who, why, when) so "
        "refusals are as auditable as grants."
    ),
)
def deny_capability(graph, call_id: str, approver_ref: str, reason: str = "") -> dict[str, Any]:
    """Registered tool wrapper — delegates to deny_capability_fn."""
    return deny_capability_fn(graph, call_id, approver_ref, reason)


TOOLS = [execute_capability, pending_approvals, approve_capability, deny_capability]
