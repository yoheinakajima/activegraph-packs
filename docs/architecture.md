# Architecture: the demo stack and Inspector UI

This repo ships a runnable demo so you can *see* packs composing on a live graph,
not just read about it. The demo has three processes plus the ActiveGraph runtime.

```
Inspector UI (React + Vite)            artifacts/activegraph-ui   port 3000
       │
       │  REST (polls every ~5s)
       ▼
API Server (Express)                   artifacts/api-server       port 5000
       │
       │  HTTP proxy  +  subprocess management
       ▼
Demo Server (standalone Python HTTP)   packs/demo_server.py       port 7788
       │
       ▼
ActiveGraph Runtime
  ├─ all packs loaded from pyproject entry points
  ├─ SQLite event log     →  data/activegraph_demo.sqlite
  └─ SQLite memory store  →  data/activegraph_memory.sqlite
```

The key convenience: **starting the API server is enough**. The Express server
spawns and health-checks the Python demo server as a child process, so you do not
launch the Python tier separately during normal development. See
`artifacts/api-server/src/lib/activegraph-process.ts`.

## Running it

```bash
pip install -e ".[dev]"                                   # Python: activegraph + all packs
pnpm install                                              # Node deps
PORT=5000 pnpm --filter @workspace/api-server run dev     # API + Python subprocess
PORT=3000 pnpm --filter @workspace/activegraph-ui run dev # Inspector UI
```

Then open `http://localhost:3000`. No API key is needed — the demo seeds the graph
from each pack's deterministic fixtures on startup and persists to SQLite in
`data/` (gitignored).

To run the Python runtime on its own:

```bash
python packs/demo_server.py     # http://localhost:7788
```

## Demo server endpoints

The Python demo server exposes a small read-mostly REST API that the Inspector
polls. The API server proxies these through.

| Endpoint | Description |
|----------|-------------|
| `GET /summary` | Object, pack, and event counts |
| `GET /graph` | All objects and relations (filterable by pack) |
| `GET /trace` | Event log (supports `limit`, `offset`, `pack`, `frame_id`) |
| `GET /packs` | Loaded packs with behaviors and object/relation types |
| `GET /frames` | Execution frames and the events grouped under each |
| `POST /chat` | Inject a chat message into the graph |
| `GET /sessions` | List chat sessions (id, user, turn count, started_at) |
| `GET /approvals` | Held capability calls (`status='policy_checking'`) + recent decisions |
| `POST /approvals` | Resolve a held call: `{call_id, decision: approve\|deny, approver_ref, note\|reason}` |
| `POST /channels/telegram/update` | Inbound Telegram update (posted by `python -m packs.telegram.poller`) |
| `GET/POST /channels/whatsapp/webhook` | WhatsApp Cloud API webhook + hub-challenge verification |
| `POST /reset` | Wipe the SQLite stores and re-seed from fixtures |

Approval state lives entirely in the graph — a pending approval *is* a
`capability_call` at `status='policy_checking'`, and decisions are
`capability_approval` / `capability_denial` objects — so the approvals API is
a thin view with no server-side bookkeeping, and it survives restarts.

## Concurrency model

The HTTP front end is threaded (`ThreadingHTTPServer`) so a slow LLM turn
never blocks another request at the socket — but the runtime is
single-threaded by design (its SQLite event store binds to its creating
thread). One dedicated **runtime executor thread** owns every graph touch;
request handlers and the schedule tick driver submit closures to it and
wait. Concurrency at the edges, strict serialization at the graph.

The **schedule tick driver** is the reference host driver for the Schedule
Pack: a daemon thread sweeps every `SCHEDULE_TICK_SECONDS` (default 10),
calling `emit_due_ticks(graph, now)` on the executor — this is the only
place wall-clock time enters the graph, exactly as chat input enters via
HTTP.

## Frames and the trace

Each unit of processing runs inside a **frame**. When you inject a source (say, a
chat message), the behaviors it triggers — and the behaviors *those* outputs
trigger, transitively — are grouped under one `frame_id`. The trace is the flat
event log; frames are the causal grouping over it. Together they let you answer
"what happened, in what order, and because of what" for any input.

## The Inspector UI

The Inspector (`artifacts/activegraph-ui`) is a React + Vite app that visualizes
the runtime in real time. It is intentionally styled as a terminal-flavored
inspector (monospace, square corners, dark, cyan accent). Navigation:

| Page | What it shows |
|------|---------------|
| **Dashboard** | Summary counts and runtime status at a glance |
| **Graph** | Objects and relations, filterable by pack |
| **Objects** | Flat object browser |
| **Trace** | The raw event log, with type and object-id filters |
| **Relations** | Graph edges grouped by relation type |
| **Patches** | Applied object mutations (`patch.applied`) and the new field values |
| **Tools** | Tool calls (`tool.responded`) — empty in the deterministic demo |
| **Failures** | Behavior errors, tool errors, and rejected patches (`patch.rejected`) |
| **Packs** | Loaded packs with their behavior maps and types |
| **Frames** | Execution frames and their grouped events |
| **Chat / Secrets / Identity** | Pack-specific views |

Several pages carry an inline **concept info** control that fetches the matching
page from the ActiveGraph docs and renders it in-app, so the relevant concept is a
click away without leaving the Inspector.

> **Note on the demo data:** the bundled demo runs every behavior in deterministic
> mode (no LLM, no API key), so it emits no `tool.*` or `behavior.failed` events.
> The Tools and Failures pages therefore show honest empty states in the demo but
> populate against a real, LLM-backed runtime.

## The mockup sandbox

`artifacts/mockup-sandbox` is a separate Vite environment for prototyping Inspector
components in isolation before integrating them into the main UI. It is a
development aid and is not part of the shipped demo stack.
