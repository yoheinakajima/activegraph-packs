"""Outbound MCP → Tool Gateway registration.

``connect_and_register`` is the whole adapter: it connects to one MCP
server, lists its tools, and registers each one as a Tool Gateway
capability — which means every MCP tool call arrives pre-governed:

  * **policy-checked** — capabilities default to ``high`` risk, so under
    the default gateway policy every third-party MCP tool is
    approval-required until the operator explicitly lowers its risk via
    ``tool_risk_overrides``. Breadth never outruns governance.
  * **recorded** — each call is a capability_call/result pair in the graph.
  * **sanitized + injection-scanned** — MCP output flows through the same
    sanitizer and untrusted-content posture as every other capability
    (gateway.execute_approved_call), and reaches the model fenced in the
    EXTERNAL CONTENT envelope.

Capability keys are ``mcp_<server>.<tool>`` — one provider namespace per
MCP server, so allow-lists can pick individual tools.

Discovery needs a live connection, so the network edge is HERE (called by
the host at startup, like the Telegram poller), not in a behavior.
Fixtures inject a fake client and never touch the network.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import Field, create_model

from packs.tool_gateway.tools import register_local_capability

from .client import MCPClient

# JSON Schema type → Python annotation for dynamic Pydantic models. MCP
# tool inputSchemas are ordinary JSON Schema objects; we map the common
# scalar/container types and fall back to Any for exotic shapes — the MCP
# server revalidates on its side, so the model here is for the LLM's
# benefit (parameter names, types, required-ness), not enforcement.
_JSON_TYPE_MAP = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def schema_model_from_json_schema(model_name: str, schema: Optional[dict]) -> type:
    """Build a Pydantic model from an MCP tool's inputSchema.

    The gateway requires an input_schema for LLM exposure (an empty schema
    would make the model call the tool with {}); a tool with no declared
    parameters gets an explicit empty model, which is honest.
    """
    properties = (schema or {}).get("properties", {}) or {}
    required = set((schema or {}).get("required", []) or [])

    fields: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        annotation = _JSON_TYPE_MAP.get((prop_schema or {}).get("type"), Any)
        description = (prop_schema or {}).get("description", "")
        if prop_name in required:
            fields[prop_name] = (annotation, Field(description=description))
        else:
            default = (prop_schema or {}).get("default", None)
            fields[prop_name] = (
                Optional[annotation] if default is None else annotation,
                Field(default=default, description=description),
            )
    return create_model(model_name, **fields)


def connect_and_register(
    server_name: str,
    client: MCPClient,
    *,
    graph=None,
    default_risk: str = "high",
    tool_risk_overrides: Optional[dict[str, str]] = None,
    credential_ref_name: Optional[str] = None,
) -> list[str]:
    """Discover one MCP server's tools and register them as capabilities.

    Returns the registered capability keys (``mcp_<server>.<tool>``).
    When *graph* is provided, an ``mcp_server`` object records the
    discovery (server info, tool list, risk classes) so the connection is
    auditable and visible in the Inspector.

    *tool_risk_overrides* maps tool name → risk class for tools the
    operator has decided to trust (e.g. ``{"search": "low"}`` makes a
    read-only search auto-approvable). Everything else stays at
    *default_risk* — approval-required by default under gateway policy.
    """
    overrides = tool_risk_overrides or {}
    server_info = client.initialize()
    tools = client.list_tools()

    registered: list[str] = []
    provider_name = f"mcp_{server_name}"
    for tool_def in tools:
        tool_name = tool_def.get("name", "")
        if not tool_name:
            continue
        risk = overrides.get(tool_name, default_risk)
        model = schema_model_from_json_schema(
            f"MCP_{server_name}_{tool_name}_Input", tool_def.get("inputSchema")
        )

        def _executor(_tool: str = tool_name, **input_data) -> dict:
            # The gateway may pass execution_context; MCP calls don't use it
            # (credentials live in the transport headers, injected at
            # connection time by the host from the Secrets Pack).
            input_data.pop("execution_context", None)
            text, is_error = client.call_tool_text(_tool, input_data)
            if is_error:
                raise RuntimeError(f"MCP tool {_tool!r} returned an error: {text[:500]}")
            return {"text": text, "server": server_name, "tool": _tool}

        spec = register_local_capability(
            provider_name,
            tool_name,
            _executor,
            input_schema=model,
            description=(
                f"[MCP:{server_name}] {tool_def.get('description', '')}".strip()
            ),
            risk_class=risk,
            credential_ref_name=credential_ref_name,
            origin=f"mcp:{server_name}",
        )
        registered.append(spec.key)

    if graph is not None:
        try:
            graph.add_object("mcp_server", {
                "name": server_name,
                "direction": "outbound",
                "server_info": server_info,
                "tool_names": [t.get("name", "") for t in tools],
                "capability_keys": registered,
                "default_risk": default_risk,
                "risk_overrides": overrides,
                "status": "connected",
                "connected_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass  # graph audit is best-effort; the registry is the source of truth

    return registered


def register_configured_servers(settings, graph=None) -> dict[str, list[str]]:
    """Connect every server in ``MCPSettings.servers`` (the host-startup path).

    Servers that fail to connect are recorded (status='unreachable' when a
    graph is present) and skipped — one broken MCP server must not take
    down the assistant. Returns {server_name: [capability keys]}.
    """
    from .client import make_client

    results: dict[str, list[str]] = {}
    for server in settings.servers:
        name = server.get("name", "")
        if not name:
            continue
        try:
            headers = dict(server.get("headers") or {})
            client = make_client(
                server.get("transport", "http"),
                url=server.get("url", ""),
                command=server.get("command"),
                headers=headers,
            )
            results[name] = connect_and_register(
                name,
                client,
                graph=graph,
                default_risk=server.get("default_risk", settings.default_tool_risk),
                tool_risk_overrides=server.get("tool_risk_overrides"),
                credential_ref_name=server.get("credential_ref_name"),
            )
        except Exception as exc:
            results[name] = []
            if graph is not None:
                try:
                    graph.add_object("mcp_server", {
                        "name": name,
                        "direction": "outbound",
                        "status": "unreachable",
                        "error": f"{type(exc).__name__}: {exc}",
                        "connected_at": datetime.now(timezone.utc).isoformat(),
                    })
                except Exception:
                    pass
    return results
