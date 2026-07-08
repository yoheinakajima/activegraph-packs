"""LLM tool proxies — chat that can act, without bypassing the gateway.

The runtime's native tool loop (@llm_behavior(tools=[...])) executes Tool
functions directly: no policy check, no credential injection, no
capability_call record. So the tools we hand a model are never raw
capabilities — they are PROXIES into this gateway:

    model calls tool
      → proxy records a capability_call in the graph   (audit before action)
      → decide_policy — the same single policy implementation
        policy_enforcer uses
      → auto-approvable: capability_approval recorded, then executed inline
        via execute_approved_call (credential injection, sanitization,
        capability_result, produces_result) and the sanitized result is
        returned to the model in the same tool turn
      → held: the call stays at status='policy_checking' and the model is
        told so — approve_capability / deny_capability (or the demo server's
        POST /approvals) resolve it later through the normal reactive path

Either way, every model-initiated action is a first-class graph object
before anything runs, and the trace reads
    llm.requested → tool.requested → capability_call → capability_approval
    → capability_result → tool.responded
with `model proposes; runtime disposes` intact inside a synchronous loop.

Double execution is impossible by construction: the proxy executes while
the call is 'approved' and records the approval after the call is already
'done'; call_executor's status guard then skips it. One approval → at most
one execution, on both paths.

Graph access: ToolContext deliberately has no graph reference, so — like
the runtime's own make_graph_query_tool — these factories close over the
Graph at construction time. Build them after the Graph exists, then hand
the Tool objects to @llm_behavior(tools=[...]) (the runtime pulls
behavior-declared Tool objects into its registry automatically).

Naming: proxy tools keep the gateway's canonical "provider.capability" key
as their tool name, so the trace and the capability registry read the same.
The chat pack's ProviderCompat sanitizes the dot at the provider wire
boundary and maps it back.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from activegraph.tools.base import Tool
from activegraph.tools.context import ToolContext

from .gateway import decide_policy, execute_approved_call
from .settings import ToolGatewaySettings
from .tools import CapabilitySpec, get_capability_spec, registered_capability_keys


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def as_llm_tool(
    graph,
    spec: CapabilitySpec,
    *,
    settings: Optional[ToolGatewaySettings] = None,
    provider_id: str = "",
    timeout_seconds: float = 30.0,
) -> Tool:
    """Wrap one registered capability as an LLM-callable Tool proxy.

    Requires ``spec.input_schema`` — a tool with an empty parameter schema
    would be called with ``{}`` by the model, which is a misconfiguration,
    not a feature. Raises ValueError with the fix if it is missing.
    """
    if spec.input_schema is None:
        raise ValueError(
            f"Capability {spec.key!r} has no input_schema, so it cannot be "
            "offered to an LLM (the model would see an empty parameter "
            "schema and call it with {}). Register it with "
            "register_local_capability(..., input_schema=YourPydanticModel)."
        )

    gw_settings = settings or ToolGatewaySettings()

    def _proxy(args: Any, ctx: ToolContext) -> dict[str, Any]:
        input_data = args.model_dump() if hasattr(args, "model_dump") else dict(args)
        frame_id = getattr(ctx.frame, "id", None)
        decision = decide_policy(spec.risk_class, gw_settings)

        # Audit before action: the capability_call exists (with its policy
        # outcome) before anything executes. Status is pre-decided here via
        # decide_policy, so policy_enforcer (which only acts on 'proposed')
        # does not double-decide; call_recorder still links the provider.
        call = graph.add_object("capability_call", {
            "provider_id": provider_id,
            "provider_name": spec.provider_name,
            "capability_name": spec.capability_name,
            "input_data": input_data,
            "credential_ref_name": spec.credential_ref_name,
            "risk_class": spec.risk_class,
            "status": "approved" if decision == "auto_approve" else "policy_checking",
            "proposed_by": ctx.behavior_name,
            "frame_id": frame_id,
            "proposed_at": _now_iso(),
            "metadata": {"initiated_by": "llm_tool_loop"},
        })

        if decision == "hold":
            return {
                "status": "held_for_approval",
                "call_id": call.id,
                "risk_class": spec.risk_class,
                "note": (
                    f"This {spec.risk_class}-risk action was recorded but needs "
                    "explicit approval before it runs. Tell the user it is "
                    "waiting for their sign-off."
                ),
            }

        # Auto-approved: execute inline so the result reaches the model in
        # this same tool turn. The approval object is recorded AFTER
        # execution — call_executor's 'approved'-status guard then skips it,
        # keeping one execution per approval.
        result = execute_approved_call(
            graph, call.id, call.data, gw_settings, executed_by=ctx.behavior_name,
        )
        approval = graph.add_object("capability_approval", {
            "call_id": call.id,
            "provider_id": provider_id,
            "provider_name": spec.provider_name,
            "capability_name": spec.capability_name,
            "input_data": input_data,
            "credential_ref_name": spec.credential_ref_name,
            "frame_id": frame_id,
            "policy_decision": "auto_approved",
            "approver": "gateway_llm_proxy",
            "approved_at": _now_iso(),
            "metadata": {"risk_class": spec.risk_class, "executed": "inline"},
        })
        # NOTE: add_relation signature is (source, target, type).
        try:
            graph.add_relation(call.id, approval.id, "approved_by")
        except Exception:
            pass

        return {
            "status": result["status"],
            "call_id": call.id,
            "output": result.get("output_data", ""),
            "error": result.get("error"),
        }

    return Tool(
        name=spec.key,
        fn=_proxy,
        description=spec.description or f"Gateway capability {spec.key}.",
        input_schema=spec.input_schema,
        output_schema=None,
        timeout_seconds=timeout_seconds,
        deterministic=False,
    )


def llm_tools_for(
    graph,
    keys: list[str],
    *,
    settings: Optional[ToolGatewaySettings] = None,
    provider_ids: Optional[dict[str, str]] = None,
) -> list[Tool]:
    """Build LLM tool proxies for the given capability keys.

    *keys* are "provider.capability" registry keys (the same strings
    register_local_capability creates). Unknown keys fail loud with what IS
    registered — an allow-list typo must not silently shrink the toolset.

    *provider_ids* optionally maps provider_name → capability_provider
    object id so call_recorder can link calls to their provider objects.
    """
    tools: list[Tool] = []
    for key in keys:
        spec = get_capability_spec(key)
        if spec is None:
            registered = registered_capability_keys()
            raise ValueError(
                f"No capability registered under {key!r}. Registered keys: "
                f"{registered or '(none)'}. Register it first with "
                "register_local_capability(provider, capability, fn, "
                "input_schema=..., description=..., risk_class=...)."
            )
        tools.append(as_llm_tool(
            graph,
            spec,
            settings=settings,
            provider_id=(provider_ids or {}).get(spec.provider_name, ""),
        ))
    return tools
