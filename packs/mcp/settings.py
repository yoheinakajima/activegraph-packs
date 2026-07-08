"""Settings for the MCP Pack (both directions)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MCPSettings(BaseModel):
    """Configuration for MCP Pack v0.1.

    Outbound (the assistant consumes MCP servers) and inbound (other agents
    consume the assistant over MCP) are configured together: one pack, one
    settings object, one place to reason about the MCP surface.
    """

    # ── outbound ────────────────────────────────────────────────────────────
    servers: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Outbound MCP servers to connect at startup. Each entry: "
            "{'name': str, 'transport': 'http'|'stdio', 'url': str (http), "
            "'command': [str] (stdio), 'headers': {str: str} (http auth), "
            "'default_risk': str, 'tool_risk_overrides': {tool: risk}, "
            "'credential_ref_name': str}. The demo server reads this from "
            "ACTIVEGRAPH_MCP_SERVERS (JSON)."
        ),
    )

    default_tool_risk: Literal["low", "medium", "high", "critical"] = Field(
        default="high",
        description=(
            "Risk class assigned to discovered MCP tools with no explicit "
            "override. 'high' means approval-required under the default "
            "gateway policy — third-party tools start untrusted and are "
            "promoted deliberately, per tool, via tool_risk_overrides."
        ),
    )

    # ── inbound ─────────────────────────────────────────────────────────────
    tokens: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Inbound bearer-token map: token → caller identifier (email, "
            "'agent:researcher', 'telegram:123', …). The identifier resolves "
            "to an Identity/Auth principal whose ROLE decides exposure. The "
            "demo server reads ACTIVEGRAPH_MCP_TOKENS "
            "('token1:you@x.com,token2:agent:foo'). Tokens are shared "
            "secrets for v1; OAuth arrives with the managed-auth work."
        ),
    )

    expose_capabilities: list[str] = Field(
        default_factory=list,
        description=(
            "Gateway capability keys offered to inbound MCP callers as tools "
            "(subject to the per-surface exposure rules). Calls run through "
            "the SAME governed path as the assistant's own tool use: "
            "recorded, policy-checked, held for approval when risky."
        ),
    )

    memory_backend_url: str = Field(
        default=":memory:",
        description=(
            "Backend the inbound memory_search surface queries — must match "
            "MemoryGatewaySettings.backend_url (same rule as ChatSettings)."
        ),
    )

    default_exposures: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "chat": ["owner"],
            "memory_search": ["owner"],
            "tools": ["owner"],
            "catalog": ["owner"],
        },
        description=(
            "Default per-surface exposure: surface → roles allowed. Seeded "
            "as mcp_exposure graph objects on first start (idempotent), then "
            "the GRAPH is the source of truth — edit via the governed "
            "mcp.set_exposure capability (owner-approved) or the graph "
            "directly. Surfaces: 'chat', 'memory_search', 'tools' (plus "
            "'tool:<capability_key>' per-tool overrides). Default: owner "
            "gets everything, everyone else nothing (fail-closed)."
        ),
    )

    unverified_token_role: str = Field(
        default="owner",
        description=(
            "Role assumed for a VALID configured token when Identity/Auth "
            "has no principals registered (single-user mode: possession of "
            "an operator-configured token is the only auth there is). Once "
            "principals exist, the token's identifier must resolve to one — "
            "unknown identifiers are rejected. Set to '' to fail closed "
            "even without principals."
        ),
    )
