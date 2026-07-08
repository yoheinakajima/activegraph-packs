# activegraph-packs Changelog

This file tracks repo-level changes. Per-pack changes are recorded in each pack's own `CHANGELOG.md`.

---

## Unreleased

- **Evolution pack v0.5.1: soak preflight + never-opaque crash detail.**
  A Replit soak RED (root cause environmental: the runtime's trial-child
  env whitelist strips `REPLIT_PYTHONPATH`, so the child cannot import
  activegraph there) surfaced two soak-side defects. The soak now runs a
  preflight before rotation 1 that launches one real minimal trial child
  and refuses to run (exit 2, clear message) on a box that cannot host
  subprocess trials, instead of accumulating silent crashes. And a
  trial-child failure is never opaque: its outcome and
  `TrialReport.detail` reach the digest and anomaly log, not just the
  soak-side assertion. The whitelist itself is a runtime security
  boundary and is untouched; the fix for legit-but-incapable
  environments is couriered to the runtime (make the child's package
  discoverability an explicit input, do not forward arbitrary platform
  env). Runbook documents the environment constraint. Fixture 24.
- **Evolution pack v0.5.0: author-frame enforced boundaries** (LLM-author
  build gate 2 met). The design review passed; its four required
  changes turn asserted trust boundaries into enforced ones, built
  ahead of the author. Charter integrity is now a gate
  (`static:reserved_paths`, first to run): a proposal targeting the
  human-PR-only charter path is refused. Drafting-record taint is
  recomputed from admitted object ids at submission, so a record that
  lies about its stored flags cannot launder taint. Admitted structured
  fields are charset-validated against the manifest identifier pattern;
  prose-shaped fields are refused. The exception message stays out of
  the author frame (closed NO). `packs/evolution/author_frame.py`,
  fixtures 21-23. The author itself stays unbuilt (remaining gates: a
  green soak and the mock-model assembly fixtures).
- **Evolution pack v0.4.0: the soak harness** (LLM-author gate 5's
  clock starts). `python -m packs.evolution.soak` runs the complete
  loop unattended on a keyless machine: seven paths per rotation
  (happy with watch window, conflict-park, disable-restart, all three
  budget nets, tainted-suspended), a fresh boot per rotation, daily
  markdown digest, anomalies recorded with tracebacks. Runbook at
  docs/soak-runbook.md (healthy output, red flags, stop conditions).
  Fixture 19 proves one full rotation in CI.
- **Drafting records rendered** (gate 3 pulled forward): the
  drafting_context schema from docs/llm-author-design.md is registered,
  submission inherits its taint union deterministically, and the review
  page shows what the author READ beside what it wrote. Fixture 20:
  tainted record suspends, loud banner, no approve button. When the
  author lands, gate 3 is a wiring step.
- **Evolution pack v0.4.1: retention concurrency verdict folded.** The
  runtime ruled the retention offline requirement per-RUN (CONTRACT
  v1.5 #2 addendum 2b): retiring a fork run is sanctioned while a live
  runtime is attached to other runs in the same store. Docs in
  `boot.retire_unpinned_trial_forks`, the demo server, design §7.2/§7.5,
  and fixture 18 now record the ruling and the two operator conditions
  the pack keeps. No behavior change; the pattern was already correct.

## v0.6.0 — v1.5 consumption: subprocess trials, retention pins, enforcement (2026-07-08)

- **Runtime floor raised to activegraph >=1.5,<2.0** (honest: this
  cycle imports `activegraph.sandbox.run_forked_trial` and the
  `activegraph.store.retention` API). The 1.5 compat pass was quiet:
  183 tests and all 20 fixture suites green before any consumption.
- **Evolution pack v0.3.0: subprocess trials** (gate 1 flips). Stage 3
  runs on the runtime's sandbox: ALL candidate execution in a
  fresh-interpreter child (fixture gate, in-sample, held-out),
  bundle-hash-verified before any import, under the runtime's three
  nets. The chassis trial driver joins the authored file set
  (`fixtures/trial_scenario.py`, gate-verified byte for byte) because
  the sandbox requires scenarios inside the pinned root; the held-out
  split now freezes at proposal creation under the approval pin. Trial
  forks persist in the store (the in-process registry is gone;
  restarts no longer force re-trials). Fixture 17: a candidate that
  spins forever at import dies in the child; the parent survives.
- **Retention pins consumed** (evolution §7.5 closes): boot
  housekeeping retires disposable trial forks through the runtime
  retention API; promoted-from fork logs refuse with
  `RetentionPinnedError` (fixture 18); the demo server runs the
  housekeeping offline, before the runtime attaches.
- **Tool Gateway v0.6.0: registration enforcement** (the Q8 chain
  closes). Armed once by the host, every native
  `register_local_capability` call checks graph-derived pack
  declarations and refuses undeclared pairs, risk-class drift, and
  disabled packs' surfaces. Fixture 7 now proves three independent
  walls against self-approval.
- **Approval channel auth** (gate 4, the demo-server half): approval
  decisions require `Authorization: Bearer $ACTIVEGRAPH_APPROVAL_TOKEN`
  (constant-time compare, 401 without it, refusals audited into the
  graph), and refuse outright when evolution is on with no token
  configured. The token authenticates the channel; the principal check
  stays the decision. Session-to-principal binding remains product
  chassis territory, stated in the docs.

## v0.5.0 — Evolution hardening: decision surface, residue, retry cap (2026-07-08)

- **Evolution pack v0.2.0: the adoption decision surface** (scare-list
  #3, the launch blocker). `packs/evolution/review.py` renders one
  proposal as one readable page from graph state alone: author banner
  first, injection flags loud, the gap, the declared surface including
  `consumes`, every gate verdict, trial numbers with the fork run id,
  and the FULL per-file source diff. Demo server: `/approvals/review`,
  plus content negotiation on `/approvals` (browsers get the review
  index, API clients keep the JSON). Approving code you have not read
  defeats the threat model; now the owner's easiest path is reading it.
- **Trial replay residue policy, resolved** (scare-list #4, design
  §7.3): a passing fork sweeps everything it created during replay
  before adoption sees it, so promote carries zero replay scaffolding
  into the parent. Shared-state patches still promote (the conflict
  check's whole subject). Sweep counts recorded on the trial.
- **Retry-capped chassis** (scare-list #5): `sweep_evolution` retries a
  conflicted adoption at most `max_conflict_retries` times (re-gate,
  re-trial at parent-now, requeue under the same approved call), then
  parks the proposal at the new terminal `needs_owner` status that
  nothing automatic touches again. The demo server tick driver uses it.
- **LLM author design, design only** (scare-list #2):
  `docs/llm-author-design.md` specifies how a drafting context is
  assembled so tool-derived and memory-derived text stays out of the
  author frame. The author stays UNBUILT and the pipeline stays
  scripted-author-only until the runtime ships subprocess trial
  isolation and the design survives review.
- Evolution fixtures 14, 15, 16 (decision surface end to end, zero
  residue after adoption, retry cap parks at needs_owner); fifteen
  scenarios total, still scripted-author, no keys, no network.

## v0.4.0 — Self-modification era: evolution pack, manifests, activegraph 1.4 (2026-07-08)

- **Evolution pack v0.1.0** (task #3, the reason everything else
  exists): agent-authored packs under governance. Static gates (nine,
  including both hash pins and the two-way surface check via the
  runtime validator), fork trials with regimes-style in-sample/held-out
  discipline and subprocess fixture gates, two-phase governed adoption
  (critical capability whose REGISTRATION refuses auto-approvable
  policies and unverified identity; the chassis applies tickets between
  frames: bundle pin, gates re-run, dry run, load + loading-state
  record, quiescent promote), immediate disable via `rt.disable_pack`
  plus boot exclusion, and boot-time re-materialization with loud
  corruption handling. Twelve deterministic acceptance fixtures,
  scripted author only. Ships DISABLED; demo server opts in via
  `ACTIVEGRAPH_EVOLUTION=1`. Design doc updated in the same commits for
  every implementation-forced decision (two-phase adoption; the honest
  apply-time-validation semantics of fixture 13; the pluggable author).
- **Manifest spec FROZEN** (except §5, provisional): the freeze
  condition was met when the evolution gates started validating real
  manifests through `activegraph.packs.manifest` inside passing
  fixtures.
- **Pack manifests, implemented** (task #4; activegraph floor raised to
  >=1.4,<2.0 because CI imports the runtime's validator — claiming 1.3
  compat would be dishonest). Every pack ships a `manifest.toml`
  generated by `scripts/generate_manifests.py` (rerunnable; rerun after
  editing a pack to refresh its hash). CI validates all 19 on every
  push via `activegraph.packs.manifest` (Q7: runtime owns schema and
  validation): schema, content hash, and the two-way surface check.
  The static AST checks for `[[surface.capabilities]]` and `consumes`
  stay in this repo by design (`scripts/manifest_tools.py`, shared by
  the generator and CI so they can never disagree). The five packs
  registering gateway capabilities now declare them on
  `Pack.capabilities` (Q8 chain step 1). `packs/_template` gains a
  manifest skeleton. Spec updates in the same change: the runtime's
  Q1-Q8 answers folded in as resolved; the BUNDLE hash (external pins
  and evolution proposal pins cover `manifest.toml` itself, closing
  manifest-only approve-then-swap) amended into §4/§5 and
  `docs/evolution-design.md` T4; `tests/test_manifests.py` proves the
  two-hash argument (a manifest-only swap passes the content hash and
  fails the bundle-hash pin). Graph-backed approvals compat checked:
  this repo's approvals were already graph-backed; suite green.
- **vc pack DELETED** (replaces the extraction plan, task #8). The pack
  was not well designed, and its extraction was only ever a dogfood for
  the manifest's multi-repo loading. Removed: `packs/vc`, `vc_bundle`
  (+ its example and `build_vc_assistant`), the pyproject entry point,
  the CI step, and every doc reference. The bundle count drops to 4 and
  the pack count to 18 + bridge. Consequence for the manifest spec: §5
  (sources, resolution, pins) loses its first consumer and is marked
  PROVISIONAL until a real pack-sources host builds against it; the
  spec's freeze condition is now "runtime Q1-Q8 answers folded + the
  evolution pack consuming the spec in its gates" (see the spec header).
- **activegraph pin: >=1.3,<2.0** (task #10). Everything the packs waited
  for is in this runtime release; the migration in full:
  - `@tool` signatures across 12 packs satisfy v1.3 registration-time
    validation (defaults beyond the `(args, ctx)` contract; patch bumps
    per pack). No behavior change.
  - **Shims retired with proof** (chat v0.4.0): `ProviderCompat`
    (tool-name wire sanitization) and the OpenAI reasoning-family
    parameter shim are deleted; the runtime owns the wire boundary
    (CONTRACT v1.3 #3). `tests/test_provider_compat.py` is now the
    retirement proof, pointed at `activegraph.llm.wire` and
    `OpenAIProvider` — including the taxonomy split (auth/request errors
    terminal, no longer retried as network flakes).
  - **Kept, with reason**: chat's `FallbackChatProvider` (mock-mode UX,
    never a runtime workaround).
  - **EmbeddingProvider seam adopted** (memory_gateway v0.4.0): both
    pack embedders implement the runtime protocol; the backend accepts
    runtime providers and the legacy seam; the runtime's
    `HashEmbeddingProvider` works behind `set_embedder()` unmodified.
  - **Trace surface**: audited; no pack read around the old surface, so
    nothing to retire. `rt.trace.events()/failures()` are available to
    the evolution pack as designed.
  - **Strict-replay audit (CONTRACT v1.3 #4.4b)**: no pack uses
    `replay_strict=True` and none subscribes to `promote.applied` today,
    so no current pack is affected. The one FUTURE subscriber is the
    evolution pack (by design); the limitation is recorded in
    `docs/evolution-design.md` §9 so its fixtures and any host enabling
    strict replay know marker-derived events do not re-derive.

## v0.3.0 — Library era: MCP, catalog, managed auth, design docs (2026-07-08)

- **Capability catalog** (tool_gateway v0.5.0, mcp v0.2.0): every
  registered capability queryable with risk class, origin (native vs
  MCP-derived), LLM-exposability, and live allow-list status. The agent
  discovers via the governed `catalog.search` capability instead of
  memorizing an allow-list; the Inspector reads GET /capabilities;
  inbound MCP callers get `catalog_search` scoped to what their role
  can reach (default owner-only exposure).
- **Managed auth** (secrets v0.2.0): OAuth 2.0 device flow behind the
  existing `resolve_credential_fn` seam. Env still wins; tokens live in
  a dedicated SQLite store (never the graph); auto-refresh ahead of
  expiry; the audit trail records WHICH source resolved each credential.
  Demo server: POST /secrets/oauth/start + /secrets/oauth/poll, with
  refresh flows surviving restarts.
- **Design docs**: `docs/evolution-design.md` (the full agent-authored
  pack lifecycle: gates, fork trials, governed adoption via promote,
  threat model, acceptance fixtures) and `docs/manifest-spec.md` (DRAFT
  pack manifest contract for the runtime loader, CI, the evolution
  pack, multi-repo loading, and pack-sources configs). Both
  adversarially reviewed; the manifest stays DRAFT until the vc
  extraction and the evolution pack consume it.
- **README reframe**: this repo is the official ActiveGraph pack
  library (general capability layer + conventions + reference chassis);
  loud security warning on the `ACTIVEGRAPH_REPLY_POLICY=open` default.
- `packs/_template` gains the mandatory fixtures skeleton.
- **MCP, both directions** (new `mcp` pack v0.1.0): outbound, any MCP
  server's tools become Tool Gateway capabilities — approval-required by
  default, promoted per tool, recorded/sanitized/scanned like every
  capability (stdlib client, no SDK dependency). Inbound, the assistant
  is itself an MCP server (`POST /mcp`): other agents can chat with it,
  search its memory (subject-scoped to the caller), or invoke exposed
  skills — bearer tokens resolve to Identity/Auth principals, graph-native
  fail-closed exposure rules decide role access, and the assistant can
  *propose* changes to its own exposure via the governed
  `mcp.set_exposure` capability (owner approves). See `docs/mcp.md`.
- **Untrusted-content posture** (tool_gateway v0.4.0): tool output reaches
  models fenced in EXTERNAL CONTENT markers; a deterministic injection
  detector records `injection_flag` audit objects (never blocks — a
  tripwire, not an oracle); approval capabilities are hard-excluded from
  LLM exposure (`NEVER_LLM_CALLABLE`). Threat model: `docs/security.md`.
- **CI**: the pytest suite now runs in CI (it previously ran only
  locally), plus fixture steps for schedule, telegram, whatsapp, and mcp.
- **Memory retrieval quality + pluggable backends** (memory_gateway v0.3.0):
  hybrid recall scoring fixes the July 2026 readiness-report §5.1 failures
  (short/keyword queries and rephrased questions now recall; every verified
  failure case is a regression test); the store is a first-class seam
  (`MemoryBackend` protocol + URL-scheme registry) with a working mem0
  adapter; bundled `OpenAIEmbedder`/`HashEmbedder` and a default factory the
  demo server wires at startup (`OPENAI_API_KEY` → hybrid recall, no key →
  lexical, never errors). See `packs/memory_gateway/CHANGELOG.md`.

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
