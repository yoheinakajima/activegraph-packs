# activegraph-packs [![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

An open-source collection of [ActiveGraph](https://pypi.org/project/activegraph/) packs, bundles, a Python demo server, and a React Inspector UI.

**What this repo is for.** This is the **official pack library for
ActiveGraph**: the general-purpose capability layer (memory, tools,
identity, communication, channels, scheduling, MCP) that assistants built
on the runtime compose from, plus the conventions that keep a multi-pack
assistant coherent — the layering model, the coordination style, the
fixture discipline, and the pack format itself
([docs/manifest-spec.md](docs/manifest-spec.md), draft). Specialized
domain packs belong in their own repos once the manifest's multi-repo
loading lands, establishing the pattern for third-party pack libraries;
the bundles and demo server here are the reference assistant chassis, and
products built on this library ship their own. [CONTRIBUTING.md](CONTRIBUTING.md)
is the canonical pack-author guide — the same bar every pack in this
library meets, human-authored or otherwise.

ActiveGraph is a reactive object-graph runtime for Python. You define objects (typed nodes), relations (typed edges), behaviors (reactive handlers that fire on mutation), and tools (callable capabilities). This repo shows how to compose 18 packs plus a bridge pack (19 entry points) into a coherent, auditable assistant architecture — no central orchestrator, no monolithic pipeline. Coordination is *emergent*: one pack writes an object, that write is an event, and the event triggers a behavior in another pack.

> **New here? Read [docs/concepts.md](docs/concepts.md) first** — it explains the
> event-sourced graph model and the central idea of this repo: the split between the
> minimal **Core Pack** and the **layered (domain) packs** that build on it.

---

## Quick Start

Pick the path that matches what you want to do. The **same code** runs in all
three — no Replit-specific build, no API key.

### Path 1 — Use the packs only (Python, no UI)

For embedding packs in your own code, running fixtures, or the standalone demo
server. No Node required.

```bash
pip install -e ".[dev]"          # activegraph + all packs (editable install)

# Option A: run the standalone demo server (HTTP API on :7788)
python packs/demo_server.py

# Option B: build a runtime in your own code
python -c "from bundles import build_assistant; \
  build_assistant().run_goal('Summarize what you can do')"
```

### Path 2 — Run the full demo locally (UI + API + runtime)

One command brings up the React Inspector UI, the Express API server, and the
Python runtime together (the API server spawns the Python process for you).

```bash
pip install -e ".[dev]"          # Python deps
pnpm install                     # Node deps
pnpm dev                         # starts the whole stack
```

> The local dev commands assume a POSIX shell (macOS / Linux / Replit). On
> Windows, run them under WSL.

Then open **http://localhost:3000**.

| Service | URL |
|---------|-----|
| Inspector UI | http://localhost:3000 |
| API server | http://localhost:5000 |
| Python runtime | http://localhost:7788 (spawned by the API server) |

Override ports with `API_PORT` / `UI_PORT`. To run the pieces in separate
terminals instead of `pnpm dev`:

```bash
PORT=5000 pnpm --filter @workspace/api-server run dev
PORT=3000 BASE_PATH=/ pnpm --filter @workspace/activegraph-ui run dev
```

### Path 3 — Develop on Replit

Open the repo on Replit and press **Run**. The workspace is preconfigured: each
artifact (UI, API server) has its own workflow, ports and inter-service routing
are wired automatically, and the preview pane shows the Inspector UI. No
environment setup needed.

> **How dual-target works.** The UI reads `PORT` / `BASE_PATH` from the
> environment when present (Replit injects them) and falls back to local
> defaults (`5173` / `/`) otherwise. Off-platform the UI proxies `/api` to the
> local API server; on Replit the platform path-routes `/api` instead. The
> Replit-only editor plugins load only when running on Replit and are silently
> skipped in a plain checkout.

### Running it as a personal assistant

The demo server is a working always-on assistant, configured entirely by
environment:

| Env var | Purpose |
|---------|---------|
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Live LLM (otherwise instructive mock mode) |
| `ACTIVEGRAPH_OWNER` | Comma-separated owner identifiers (`you@x.com,telegram:123,whatsapp:1555...`) — seeded as owner principals |
| `ACTIVEGRAPH_REPLY_POLICY` | `open` (default) \| `known` \| `owner_only` — who gets conversational replies (fail-closed once set). ⚠️ **The `open` default exists so the local demo works on first run. Before exposing the assistant on any reachable channel (Telegram, WhatsApp, MCP, a public URL), set `known` or `owner_only` and seed `ACTIVEGRAPH_OWNER` — otherwise anyone who finds the bot gets full LLM replies on your API key.** |
| `SCHEDULE_TICK_SECONDS` | Schedule sweep period (default 10; `<=0` disables) |
| `TELEGRAM_BOT_TOKEN` | Registered via `POST /secrets` or env; enables Telegram delivery. Run `python -m packs.telegram.poller` |
| `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN` | WhatsApp Cloud API delivery + webhook verification |
| `ACTIVEGRAPH_MCP_TOKENS` | Inbound MCP auth: `token:identifier` pairs — other agents can chat, search memory, or call exposed skills over `POST /mcp` (see [docs/mcp.md](docs/mcp.md)) |
| `ACTIVEGRAPH_MCP_SERVERS` | Outbound MCP servers (JSON) — their tools become governed capabilities, approval-required by default |
| `ACTIVEGRAPH_MCP_EXPOSE` | Capability keys offered to inbound MCP callers (default: the chat allow-list) |
| `ACTIVEGRAPH_TOKEN_DB` | Managed OAuth token store (default `data/activegraph_tokens.sqlite`) — connect accounts via `POST /secrets/oauth/start` + `/poll`; tokens never enter the graph |

Chat is agentic out of the box (`web.fetch_url` +
`schedule.create_reminder` on the tool allow-list, plus any tools
discovered from configured MCP servers): every model-initiated
action flows through the Tool Gateway — recorded before it runs,
policy-checked, credentials injected at execution time, output sanitized,
scanned for prompt-injection patterns, and fenced as untrusted external
content before the model reads it ([docs/security.md](docs/security.md)) —
and high-risk actions wait in `GET /approvals` for an explicit, verified
approve/deny. Everything above is reconstructible from `GET /trace`.

### State & persistence

The demo persists its event log and memory store to SQLite under `data/` by
default, so state survives restarts when running locally. Point them elsewhere
(e.g. durable storage) with `ACTIVEGRAPH_DB` and `ACTIVEGRAPH_MEMORY_DB`. On an
ephemeral or autoscale deployment the filesystem is **not** durable — the graph
simply re-seeds from pack fixtures on each cold start, and `POST /reset` re-seeds
on demand.

---

## Core and Layered Packs

The organizing idea of this repo is a two-tier pack model.

- **The Core Pack** (`packs/core`) is the universal substrate: 7 object types and 7
  relation types (`source`, `observation`, `task`, `action`, `artifact`,
  `memory_candidate`, `evaluation`) that every other pack speaks. It depends on
  nothing and stays deliberately minimal — no people, companies, claims, or
  documents. Keeping Core tiny is what keeps it a shared *lingua franca* instead of
  a universal ontology that domain packs have to fight.

- **Layered packs** are everything else. Each documents `requires = ["core"]` (some
  also require `communication`) and optionally cooperates with other packs via
  `integrates_with`, which must be optional — packs degrade gracefully when an
  integration is absent. (These dependency declarations are conventions recorded in
  each pack's README and `__init__.py` and respected through load order, not
  enforced kwargs on `Pack(...)`.) Layered packs split into *infrastructure* (tool gateway,
  secrets, memory, identity, agent profile, entity), *communication* (channel-neutral
  messaging plus chat/email adapters), and *domain* verticals (research, vc,
  codebase, team_ops, meeting). Domain packs map their outputs back to Core
  primitives, so infrastructure packs never need to know they exist.

Full explanation, dependency graph, and the invariants that hold it together:
**[docs/concepts.md](docs/concepts.md)**.

## What's in the box

### Packs

| Pack | Description |
|------|-------------|
| `core` | Universal primitive layer: source, observation, task, action, artifact, memory_candidate, evaluation |
| `tool_gateway` | Capability execution gateway — normalizes, policy-checks, and records all external tool/API/MCP calls |
| `secrets` | Credential reference management — actual secrets never enter the model context or graph |
| `memory_gateway` | Full memory lifecycle: evaluates candidates, stores accepted items (SQLite), retrieval with keyword ranking |
| `identity_auth` | Identity resolution and permission checking — resolves sources to Principals, enforces role-based access |
| `agent_profile` | Assistant identity: goals, personality, standing instructions, scoped by channel and audience role |
| `entity` | Canonical extraction and deduplication for real-world entities (people, orgs, projects, products, repos) |
| `communication` | Channel-neutral semantic layer: threads, messages, intents, response candidates |
| `schedule` | Graph-native scheduling: schedules, ticks, heartbeats — proactive behavior with the clock at the edge |
| `chat` | Chat adapter — the conversation engine (sessions, memory, reply gating, agentic responses) every interactive channel reuses |
| `telegram` | Telegram transport adapter — inbound updates → chat_input; outbound replies as policy-checked gateway sends |
| `whatsapp` | WhatsApp Cloud API transport adapter — the structural mirror of telegram |
| `mcp` | Bidirectional MCP: outbound servers' tools become governed capabilities; inbound, the assistant is an MCP server other agents can use (token auth, role-scoped exposure, full audit) |
| `email` | Email adapter — threading, deduplication, draft formatting, approval-gated outbound sends |
| `research` | Knowledge tracking for academic and applied research: papers, claims, hypotheses |
| `codebase` | Engineering workflow tracking: repos, issues, PRs, architecture decisions, dependency auditing |
| `team_ops` | Project management layer extending Core tasks with assignments, milestones, workload estimation |
| `meeting` | Meeting processing: decision extraction, action item tracking, automated summarization |

Plus `packs/bridges/` — a Diligence-Core bridge that maps the bundled ActiveGraph Diligence pack outputs to Core objects (source, observation, artifact, evaluation).

### Bundles

| Bundle | Contents |
|--------|----------|
| `assistant` | core, tool_gateway, secrets, memory_gateway, agent_profile, identity_auth, communication, schedule, chat |
| `messaging_assistant` | assistant bundle + telegram, whatsapp |
| `email_assistant` | assistant bundle + email, entity |
| `research_bundle` | core, tool_gateway, memory_gateway, research |

A bundle is a preset list of packs with a factory function — not a new pack with its own ontology.

```python
from bundles import build_email_assistant

rt = build_email_assistant()
rt.run_goal("Triage today's inbox")
```

---

## Architecture

The demo has three components:

```
Inspector UI (React + Vite)
       |
       |  REST (polls every ~5s)
       v
API Server (Express, port 5000)
       |
       |  HTTP proxy  |  subprocess management
       v
Demo Server (Python, port 7788)
       |
       v
ActiveGraph Runtime
  ├─ 16 packs loaded (15 + diligence_core_bridge)
  ├─ SQLite event log     →  data/activegraph_demo.sqlite
  └─ SQLite memory store  →  data/activegraph_memory.sqlite
```

The demo server is a standalone Python HTTP server (`packs/demo_server.py`). The API server (Express) proxies requests from the React UI and manages the Python process as a subprocess — so starting the API server is enough to get the full stack running.

For the full architecture, the frames-vs-trace model, and a tour of the Inspector UI pages, see **[docs/architecture.md](docs/architecture.md)**.

### Demo server endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /summary` | Object counts, pack counts, event counts |
| `GET /graph` | All objects and relations (filterable by pack) |
| `GET /trace` | Event log (supports limit, offset, pack, frame_id filters) |
| `GET /packs` | Loaded packs with their behaviors and object/relation types |
| `GET /frames` | Execution frames and their events |
| `POST /chat` | Inject a chat message into the graph |
| `GET /sessions` | List chat sessions (id, user, turn count, started_at) |
| `GET /approvals` | Held capability calls + recent approve/deny decisions |
| `POST /approvals` | Resolve a held call: `{call_id, decision: approve\|deny, approver_ref}` |
| `POST /channels/telegram/update` | Inbound Telegram update (posted by the long-poll driver) |
| `GET/POST /channels/whatsapp/webhook` | WhatsApp Cloud API webhook (+ Meta's hub-challenge verification) |
| `POST /reset` | Wipe SQLite stores and re-seed from fixtures |

---

## Building a new pack

Copy the template scaffold:

```bash
cp -r packs/_template packs/my_pack
```

Every pack has five source files:

```
packs/my_pack/
  __init__.py        # Exports `pack` and `MyPackSettings`
  object_types.py    # Pydantic schemas, ObjectType/RelationType lists
  behaviors.py       # @behavior and @llm_behavior handlers
  tools.py           # @tool decorated functions (may be empty)
  settings.py        # Pydantic settings class (all fields must have defaults)
  prompts/           # .md prompt files with TOML frontmatter (LLM behaviors)
  fixtures/          # .yaml scenario files for testing without an API key
  README.md          # Behavior map, object types table, usage examples
  CHANGELOG.md       # Required; starts at v0.1.0
```

Register the pack in `pyproject.toml`:

```toml
[project.entry-points."activegraph.packs"]
my_pack = "packs.my_pack:pack"
```

Then reinstall: `pip install -e ".[dev]"`.

The design principle: packs coordinate by emitting graph-visible outputs that trigger other behaviors — not by calling each other directly and not through a central coordinator. See `activegraph-direction-report.md` for the full architecture rationale.

---

## Running fixtures

Each pack ships fixture scenarios in `fixtures/*.yaml` that run without an LLM or API key. All behaviors run in deterministic mode (`deterministic=True`), so no credentials are needed.

Run a single pack's fixture suite:

```bash
python packs/core/fixtures/run_fixtures.py
python packs/vc/fixtures/run_fixtures.py
python packs/memory_gateway/fixtures/run_fixtures.py
# pattern: python packs/<pack_name>/fixtures/run_fixtures.py
```

Run the cross-pack integration suites:

```bash
# Full cross-pack integration (all packs together)
python packs/fixtures/cross_pack_integration.py

# Communication + Chat + Email integration
python packs/fixtures/comm_chat_email_integration.py

# Identity + Profile + Entity integration
python packs/fixtures/identity_profile_entity_integration.py

# Chat long-term memory across sessions (write → restart → recall)
python packs/fixtures/chat_memory_cross_session.py
```

The demo server also loads all fixture data on startup — `POST /reset` re-seeds from scratch if you want a clean run.

---

## Documentation

Full docs index: **[docs/README.md](docs/README.md)**.

- **[docs/concepts.md](docs/concepts.md)** — Core and Layered Packs: the event-sourced graph substrate, the Core-vs-layered model, how coordination emerges without an orchestrator, and the invariants. **Start here.**
- **[docs/architecture.md](docs/architecture.md)** — The demo stack (Inspector UI → API server → Python runtime), demo server endpoints, frames vs. the trace, and the Inspector UI pages.
- **[docs/long-term-memory.md](docs/long-term-memory.md)** — Conversation-driven long-term memory: how the assistant builds and recalls durable, cross-session, per-user memory with no LLM or API key, and the swappable seams for write-path ingestion, the storage backend (e.g. mem0), and embedding-based retrieval.

---

## Reports

- **[activegraph-builder-report.md](activegraph-builder-report.md)** — Honest field report from building this repo: what clicked, what didn't, rough edges in the ActiveGraph API, and notes for the ActiveGraph maintainers. Includes the confusing relation field naming, re-entrancy footgun, `@tool` callability issue, and a correction on the built-in persistence layer.

- **[activegraph-assistant-upgrade-plan.md](activegraph-assistant-upgrade-plan.md)** — The
  verified review of the July 2026 hands-on agent evaluation and the six-phase
  personal-assistant upgrade plan implemented in v0.2.0 (trust loop, agentic
  chat, reply gating, schedule pack, messenger adapters, memory curation).

- **[activegraph-direction-report.md](activegraph-direction-report.md)** — 29-section architecture direction document covering the design philosophy behind this pack system: kernel vs. Core Pack, behavior specification as the primary developer interface, frames instead of a turn coordinator, memory design, Tool Gateway, pack dependencies, bundles, and the key invariants that should never be violated.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines, the pack hygiene checklist, and the design rules that keep the codebase coherent.

The one-paragraph version: packs should degrade gracefully (hard-require only what they truly need), coordinate through graph-visible outputs rather than function calls, and ship fixtures that work without any API key.

---

## License

Apache 2.0. See [LICENSE](LICENSE).
