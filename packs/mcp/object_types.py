"""MCP Pack object and relation types — v0.1.

Three object types, all audit-first:

  mcp_server   — a connection record for an outbound MCP server (what was
                 discovered, at what risk classes, or why it was unreachable).
  mcp_exposure — an inbound exposure rule: which SURFACE (chat, memory_search,
                 tools, or a per-tool override) is visible to which ROLES.
                 The graph is the source of truth for inbound exposure;
                 edits go through the governed mcp.set_exposure capability.
  mcp_access   — one inbound MCP call: who, what surface, allowed or refused,
                 why. The access log other assistants don't have.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from activegraph.packs import ObjectType, RelationType


class MCPServer(BaseModel):
    """Audit record of an outbound MCP server connection."""

    name: str = Field(description="Server name (capability keys are mcp_<name>.<tool>).")
    direction: str = Field(default="outbound")
    server_info: dict[str, Any] = Field(default_factory=dict)
    tool_names: list[str] = Field(default_factory=list)
    capability_keys: list[str] = Field(default_factory=list)
    default_risk: str = Field(default="high")
    risk_overrides: dict[str, str] = Field(default_factory=dict)
    status: str = Field(default="connected", description="connected | unreachable")
    error: Optional[str] = Field(default=None)
    connected_at: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class MCPExposure(BaseModel):
    """An inbound exposure rule: surface → roles.

    Surfaces: 'chat', 'memory_search', 'tools' (the generic gate for every
    exposed capability), or 'tool:<capability_key>' to override one tool.
    A surface with no enabled exposure object is NOT exposed — fail closed.
    """

    surface: str = Field(description="chat | memory_search | tools | tool:<key>")
    roles: list[str] = Field(
        default_factory=list,
        description="Principal roles allowed to use this surface.",
    )
    enabled: bool = Field(default=True)
    note: str = Field(default="", description="Why this rule exists (audit).")
    updated_at: str = Field(default="")
    updated_by: str = Field(default="", description="Who last changed this rule.")
    metadata: dict[str, Any] = Field(default_factory=dict)


class MCPAccess(BaseModel):
    """Audit record of one inbound MCP call."""

    method: str = Field(description="JSON-RPC method (tools/list, tools/call, …).")
    surface: str = Field(default="", description="Surface addressed (chat, tool:…, …).")
    caller: str = Field(default="", description="Resolved caller identifier ('' = anonymous).")
    role: Optional[str] = Field(default=None, description="Resolved principal role.")
    allowed: bool = Field(default=False)
    reason: str = Field(default="")
    at: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


OBJECT_TYPES = [
    ObjectType(
        name="mcp_server",
        schema=MCPServer,
        description="Connection/discovery record for an outbound MCP server.",
    ),
    ObjectType(
        name="mcp_exposure",
        schema=MCPExposure,
        description=(
            "Inbound exposure rule: which surface is visible to which roles. "
            "Graph-native config — edited via the governed mcp.set_exposure "
            "capability, so every change is approved and audited."
        ),
    ),
    ObjectType(
        name="mcp_access",
        schema=MCPAccess,
        description="Audit record of one inbound MCP call (who, what, allowed, why).",
    ),
]

RELATION_TYPES: list[RelationType] = []
