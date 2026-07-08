# MCP: the assistant as tool consumer and as tool

The MCP Pack (`packs/mcp`) is bidirectional. Outbound, the assistant
consumes any MCP server's tools — pre-governed by the Tool Gateway.
Inbound, the assistant **is** an MCP server: another agent (a Claude
session, an orchestrator, a sibling assistant) can chat with it, search
its memory, or invoke selected skills, under token auth and role-scoped
exposure rules. Zero new dependencies in either direction: the protocol
layer is stdlib (`packs/mcp/client.py`), and the inbound endpoint speaks
plain-JSON streamable HTTP.

---

## Outbound: governed breadth

```python
from packs.mcp.client import make_client
from packs.mcp.registry import connect_and_register

client = make_client("http", url="https://mcp.example.com/mcp",
                     headers={"Authorization": "Bearer ..."})
keys = connect_and_register("example", client, graph=rt.graph)
# → ["mcp_example.search", "mcp_example.create_issue", ...]
```

Each discovered tool becomes a gateway capability (`mcp_<server>.<tool>`)
with a dynamically built parameter schema, so it can join the chat tool
allow-list like any local capability. The governance properties this buys,
none of which raw MCP has:

- **Untrusted by default.** Discovered tools get risk class `high` —
  approval-required under the default policy. A model calling one gets
  `held_for_approval`; the owner resolves it in `/approvals`. Promote
  individual tools deliberately: `tool_risk_overrides={"search": "low"}`.
- **Recorded.** Every call is a `capability_call` → `capability_result`
  pair in the graph, with the MCP caller/tool named.
- **Sanitized + injection-scanned.** MCP output flows through the same
  sanitizer and untrusted-content posture as every capability (see
  `docs/security.md`), and reaches the model fenced as EXTERNAL CONTENT.

On the demo server, configure outbound servers by environment:

```bash
ACTIVEGRAPH_MCP_SERVERS='[{"name": "example", "transport": "http",
  "url": "https://mcp.example.com/mcp",
  "tool_risk_overrides": {"search": "low"}}]'
```

Discovered tools join the chat allow-list automatically (high-risk ones
are held on call — that is the designed UX for untrusted breadth). An
unreachable server is recorded as an `mcp_server` object with
`status="unreachable"` and skipped; it never blocks startup.

Transports: `http` (streamable HTTP, JSON or SSE responses,
`Mcp-Session-Id` echoed) and `stdio` (newline-delimited JSON-RPC over a
subprocess, e.g. `{"transport": "stdio", "command": ["npx", "-y",
"@modelcontextprotocol/server-filesystem", "/data"]}`).

---

## Inbound: the assistant as an MCP server

The demo server mounts the endpoint at `POST /mcp`. Point any MCP client
at it (streamable HTTP) with a bearer token:

```bash
ACTIVEGRAPH_MCP_TOKENS='sekrit-token:you@example.com'   # token:identifier
ACTIVEGRAPH_OWNER='you@example.com'                     # identifier → owner principal
```

What a caller can do is decided by three layers:

1. **Auth** — the token resolves to an identifier, the identifier to an
   Identity/Auth principal, and the principal's **role** is the unit of
   exposure. Invalid token → refused. No token → anonymous → nothing.
   Another agent gets its own token and principal (role `collaborator`),
   so it is governed exactly like a human contact. With no principals
   registered at all, a valid token falls back to
   `MCPSettings.unverified_token_role` (default `owner` — single-user
   mode; set it to `""` to fail closed).
2. **Exposure** — graph-native `mcp_exposure` rules map surfaces to roles.
   Defaults: the owner sees everything (`chat`, `memory_search`, the
   exposed tools); everyone else sees nothing. Fail-closed: no enabled
   rule, no access. The latest rule per surface governs.
3. **Gateway policy** — exposed capabilities keep their risk classes; an
   inbound call to a high-risk skill is recorded and held for approval,
   not executed.

The surfaces:

| Tool | What it does |
|------|--------------|
| `chat` | Full chat pipeline with the caller's identity — reply gating, memory scoping, persona shaping, and the assistant's own governed tool use all apply. |
| `memory_search` | Memory recall **subject-scoped to the caller** (plus global memories) — one caller never reads another user's memories. |
| exposed capabilities | `ACTIVEGRAPH_MCP_EXPOSE` keys (default: the chat allow-list), run through the governed gateway path with `proposed_by="mcp:<caller>"`. |

Every inbound call — grant or refusal — is an `mcp_access` audit object.

### Editing exposure: config the agent can propose

Exposure rules are edited through the governed `mcp.set_exposure`
capability (risk `high`), which is on the demo chat allow-list. That means
the assistant itself can *propose* a change to its own MCP surface —
"expose memory_search to my research agent?" — and the proposal is a held
`capability_call` the owner approves or denies. Config edits are
capability calls: recorded, approved, auditable, revertible by a
counter-edit. Humans can call `packs.mcp.server.set_exposure_fn` directly
or approve via `/approvals`.

### Trying it with curl

```bash
curl -s -X POST localhost:5000/api/activegraph/mcp \
  -H 'Authorization: Bearer sekrit-token' -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

(Direct to the Python runtime: `localhost:7788/mcp`.)

---

## What v1 does not do

- **Token auth is shared-secret.** Fine for personal/agent-to-agent use;
  OAuth arrives with the managed-auth work (secrets pack extension).
- **No SSE push on the inbound endpoint** — plain JSON responses only.
  Compatible with standard streamable-HTTP clients; server-initiated
  messages (sampling, notifications) are out of scope.
- **Outbound resources/prompts are not consumed** — tools only. The seam
  (`MCPClient._rpc`) is there when a use case shows up.
