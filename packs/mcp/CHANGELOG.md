# MCP Pack Changelog

## v0.1.0 — Initial release (2026-07-08)

The P0 breadth item from the July 2026 agent-readiness report, plus the
inbound direction: the assistant as an MCP server.

### Added
- **Outbound adapter**: stdlib MCP client (`client.py` — stdio +
  streamable HTTP transports, injectable for tests);
  `registry.connect_and_register` turns a server's tools into Tool
  Gateway capabilities (`mcp_<server>.<tool>`) with dynamically built
  parameter schemas. Discovered tools default to risk `high`
  (approval-required) until promoted per tool via `tool_risk_overrides`.
  Unreachable servers are recorded and skipped, never fatal.
- **Inbound server** (`server.MCPGateway`): bearer-token auth resolving
  to Identity/Auth principals; graph-native fail-closed `mcp_exposure`
  rules (defaults: owner everything, others nothing; latest rule per
  surface wins); surfaces `chat` (full pipeline, caller-identified),
  `memory_search` (subject-scoped to the caller), and exposed gateway
  capabilities via the governed call path; `mcp_access` audit object for
  every call, grant or refusal.
- **Governed exposure editing**: `mcp.set_exposure` gateway capability
  (high risk → owner approval) — the assistant can propose changes to
  its own MCP surface; the owner decides; the graph remembers.
- Object types `mcp_server`, `mcp_exposure`, `mcp_access`.
- Demo server integration: `POST /mcp` endpoint,
  `ACTIVEGRAPH_MCP_TOKENS` / `ACTIVEGRAPH_MCP_SERVERS` /
  `ACTIVEGRAPH_MCP_EXPOSE` environment wiring, discovered tools joined
  to the chat allow-list.
- Fixtures: four deterministic scenarios (no network, no SDK, no key).
