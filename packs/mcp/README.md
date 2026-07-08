# MCP Pack — v0.1

Bidirectional Model Context Protocol support: the assistant consumes MCP
servers' tools (pre-governed by the Tool Gateway), and **is** an MCP server
other agents can talk to. Full guide: [`docs/mcp.md`](../../docs/mcp.md);
threat model: [`docs/security.md`](../../docs/security.md).

## Outbound (consume MCP servers)

```python
from packs.mcp.client import make_client
from packs.mcp.registry import connect_and_register

client = make_client("http", url="https://mcp.example.com/mcp")
keys = connect_and_register("example", client, graph=rt.graph)
# each tool → gateway capability "mcp_example.<tool>", risk 'high' by default
```

- Discovered tools are **approval-required until promoted**
  (`tool_risk_overrides={"search": "low"}`).
- Every call is recorded, policy-checked, sanitized, injection-scanned,
  and fenced as EXTERNAL CONTENT before it reaches the model.
- stdlib transports (`http` streamable, `stdio`) — no SDK dependency;
  fixtures inject a fake transport.

## Inbound (be an MCP server)

`server.MCPGateway` handles JSON-RPC (`initialize`, `tools/list`,
`tools/call`); the demo server mounts it at `POST /mcp`.

- **Auth**: bearer token → identifier → Identity/Auth principal; the
  principal's role is the unit of exposure.
- **Exposure**: graph-native `mcp_exposure` rules (fail-closed; defaults:
  owner everything, others nothing; latest rule per surface wins). Edited
  via the governed `mcp.set_exposure` capability — the assistant can
  PROPOSE changes to its own surface; the owner approves.
- **Surfaces**: `chat` (full pipeline, caller-identified),
  `memory_search` (subject-scoped to the caller), and exposed gateway
  capabilities (held when risky).
- **Audit**: every call, grant or refusal, is an `mcp_access` object.

## Object types

| Name | Description |
|---|---|
| `mcp_server` | Outbound connection/discovery record (or why unreachable) |
| `mcp_exposure` | Inbound exposure rule: surface → roles |
| `mcp_access` | One inbound call: who, what, allowed, why |

## Settings (`MCPSettings`)

| Field | Default | Description |
|---|---|---|
| `servers` | `[]` | Outbound servers to connect at startup |
| `default_tool_risk` | `"high"` | Risk for discovered tools (approval-required) |
| `tokens` | `{}` | Inbound token → identifier map |
| `expose_capabilities` | `[]` | Gateway keys offered inbound |
| `memory_backend_url` | `":memory:"` | Backend for inbound memory_search |
| `default_exposures` | owner-full | Seeded exposure rules (idempotent) |
| `unverified_token_role` | `"owner"` | Role for valid tokens when no principals exist (`""` = fail closed) |

## Demo server environment

| Env var | Purpose |
|---|---|
| `ACTIVEGRAPH_MCP_TOKENS` | `token:identifier` pairs, comma-separated |
| `ACTIVEGRAPH_MCP_SERVERS` | Outbound servers (JSON list) |
| `ACTIVEGRAPH_MCP_EXPOSE` | Capability keys offered inbound (default: chat allow-list) |

## Fixtures

```bash
python packs/mcp/fixtures/run_fixtures.py
```

Four scenarios: governed outbound (held → approved → done), poisoned
response (flagged + fenced), inbound auth/exposure matrix with audit
trail, and the governed exposure edit (propose → approve → live).
