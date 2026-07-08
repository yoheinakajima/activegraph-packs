# activegraph-packs Changelog

This file tracks repo-level changes. Per-pack changes are recorded in each pack's own `CHANGELOG.md`.

---

## v0.2.0 — Personal-assistant upgrade (2026-07-08)

The six-phase upgrade from `activegraph-assistant-upgrade-plan.md`: the
verified findings of the July 2026 agent evaluation, implemented.

### New packs (3)

| Pack | Description |
|---|---|
| `schedule` | Graph-native scheduling: schedules, ticks, heartbeats — the clock lives at the edge, fixtures are synthetic-time |
| `telegram` | Telegram transport adapter: updates → chat_input; outbound replies as policy-checked gateway sends |
| `whatsapp` | WhatsApp Cloud API transport adapter — the structural mirror of telegram |

### Highlights

- **Trust loop closed** (tool_gateway v0.3.0): held capability calls are now
  resolvable — approve/deny tools with verified approvers, capability_denial
  audit objects, `GET/POST /approvals`. The graph is the single source of
  truth for approval state.
- **Agentic chat** (chat v0.3.0): `ChatSettings.tool_allow_list` wires
  gateway PROXY tools into the native LLM loop — every model-initiated
  action is recorded, policy-checked, credential-injected, and sanitized.
  Deterministic intent routing (`communication.intent_router`) covers the
  zero-LLM path.
- **Reply gating** (chat + communication + identity_auth): who gets a full
  reply is policy ('open'/'known'/'owner_only'), decided pre-LLM, fail-closed,
  with audience-aware profile shaping. Strangers cost zero tokens.
- **Memory curation** (memory_gateway v0.2.0 + core v0.1.2): questions are
  never memorized; guidance from non-conversational third-party content is
  rejected with a written rationale ("documents don't give orders");
  same-frame recall exclusion; `auto_accept_categories` finally works.
- **Repo-wide relation integrity fix**: 80 `add_relation` calls across 14
  packs passed arguments in the wrong order, writing garbage edges since
  v0.1.0. Fixed everywhere, with the fixture assertions and the demo
  server's `/graph` decoding that had normalized the bug.
- **Provider compatibility** (chat llm.py): pack-scoped tool names sanitized
  at the wire boundary; reasoning-model parameter translation; honest
  fallback errors.
- **Demo server**: sessions/approvals/channel-webhook endpoints, schedule
  tick driver, and a single runtime-executor thread under a threaded HTTP
  front end.

### Bundles

`messaging_assistant` (assistant + telegram + whatsapp) joins the four
existing bundles; `assistant` now includes `schedule`.

---

## v0.1.0 — Initial release (2026-06-03)

### Packs (15 + bridge)

| Pack | Description |
|---|---|
| `core` | Universal primitives: source, observation, task, action, artifact, memory_candidate, evaluation |
| `tool_gateway` | Capability execution with policy checks, credential injection, and output sanitization |
| `secrets` | Credential reference management — secrets never enter model context |
| `memory_gateway` | Memory lifecycle: candidate → evaluation → storage → retrieval → ranking |
| `agent_profile` | Agent goals, personality, standing instructions, and behavior-scoped context assembly |
| `identity_auth` | Principal resolution, role hierarchy, AuthContext, permission checking |
| `communication` | Channel-neutral semantic layer: CommThread, CommMessage, CommIntent, ResponseCandidate |
| `chat` | Chat adapter: chat_input → CommMessage → ChatTurn with session continuity |
| `email` | Email adapter: email_message → Source + CommMessage + EmailThread; draft + approval gate |
| `entity` | Canonical entity deduplication: extraction, resolution, merge candidate/decision flow |
| `research` | Paper ingestion, claim extraction, idea atoms, hypothesis generation |
| `vc` | VC/investor assistant: founder tracking, deal rounds, investment memos |
| `codebase` | Codebase analysis: PR/issue ingestion, tech radar, debt observations |
| `team_ops` | Team and operations: standup, OKR, retro, project tracking |
| `meeting` | Meeting transcript ingestion, decision extraction, action item creation, summary |
| `bridges/diligence_core_bridge` | Maps Diligence pack objects to Core primitives (document→source, claim→observation, memo→artifact, risk→evaluation) |

### Bundles (4)

| Bundle | Packs | Use case |
|---|---|---|
| `assistant` | core + tool_gateway + secrets + memory_gateway + agent_profile + identity_auth + communication + chat | Base interactive assistant |
| `email_assistant` | assistant + email + entity | Email-capable assistant with entity tracking |
| `vc_bundle` | email_assistant + diligence + diligence_core_bridge + vc + meeting | Full VC / investor assistant |
| `research_bundle` | core + tool_gateway + memory_gateway + communication + chat + research | Research pipeline (headless-friendly) |

### Infrastructure

- **Inspector UI** — React inspector for live graph state, event trace, behavior maps, and pack capabilities (TypeScript, `artifacts/activegraph-ui`)
- **API Server** — Express 5 API server bridging the Inspector UI to the Python runtime (TypeScript, `artifacts/api-server`)
- **Demo server** — `python packs/demo_server.py` — runs the ActiveGraph runtime on port 7788 (or `ACTIVEGRAPH_PORT`)
- **SQLite persistence** — `persist_to` parameter on `build_assistant()` for durable event logs; `Runtime.load(path)` for resume
- **Fixture runners** — every pack ships `fixtures/run_fixtures.py`; no LLM or API key required
- **Cross-pack integration fixtures** — three multi-pack scenarios in `packs/fixtures/`
- **GitHub Actions CI** — `.github/workflows/ci.yml` runs all 16 fixture runners + 3 integration suites on push and PR

### OSS hygiene

- `LICENSE` (Apache 2.0, 2026)
- `CONTRIBUTING.md` (pack authoring guide, hygiene checklist, design rules)
- `README.md` (project overview, quick start, pack table, bundle table, architecture diagram)
- `activegraph-builder-report.md` (builder log)
- `activegraph-direction-report.md` (architecture direction report, 29 sections)
- `.gitignore` hardened with `.env*`, `*.key`, `*.pem`, `*.secret` patterns
- All packs have `README.md` and `CHANGELOG.md`
