# activegraph-packs Changelog

This file tracks repo-level changes. Per-pack changes are recorded in each pack's own `CHANGELOG.md`.

---

## Unreleased

- **H2: runtime-version preflight at `import packs`.** A pre-v1.9
  runtime used to die mid-import with `CapabilityDecl.__init__() got an
  unexpected keyword argument 'action_class'`; it now raises
  `RuntimePreflightError` with an actionable install-from-source
  message. Feature detection (`CapabilityDecl.action_class`) decides —
  version strings lie in both directions for editable installs; the
  string only feeds the message. `ACTIVEGRAPH_PACKS_SKIP_PREFLIGHT=1`
  exists solely so the doctor can run on a broken runtime.

- **H4: `python -m packs.doctor`.** Noninteractive environment
  diagnostic built from the three real fresh-machine incidents: runtime
  version + v1.9 feature floor (the H2 check as a pass/fail line),
  package shadowing (a stray `activegraph` dir on `sys.path` vs the pip
  install — resolved `__file__` always printed), and replay
  artifact-store coherence between importer and normalizer settings
  (the B1 `ReplayUnavailableError` gotcha). Extras: Python ≥ 3.11,
  `--store` writability, pack entry-point resolution, and a manifest
  content-hash spot-check. Exit 0/1; `--json` for tooling; README
  section under "Doctor".

- **P6: promotion-loop wiring (ADR 0018 automation stage).** Memory
  artifacts gain versioned promotion beyond admission: reliability
  evidence generates promote/demote proposals
  (`memory.promotion.reliability@1`), explicit approval emits
  `memory.promoted` keyed `(artifact_id, artifact_version)` — nothing
  promotes silently. Promoted skill and memory versions earn
  `replay.verified` keyed `(subject_id, subject_version)` from recorded
  re-runs (fork-trial for skills, recorded admission/retrieval re-checks
  for memory; `reference_only` lineage fails loudly per ADR 0015).
  Sustained prediction accuracy (>= 8 predictions, >= 90%, R0-R2 only,
  no backfill — `tool_policy.standing_scope.prediction_accuracy@1`)
  earns a standing-scope `tool_policy` candidate promoted only by a
  verified approver; the gateway's R2 grant now REQUIRES a promoted
  standing scope (SCORING_CONTRACT: the ceiling never auto-approves
  every R2 capability); degradation demotes naming the missed
  predictions, reliability harm demotes via the guard behavior, and
  recovery never auto-re-promotes. Per-pack details in the
  tool_gateway, memory_gateway, and skills changelogs.


- **P10: packs adopt `ctx.embed`.** First-party memory-gateway embedding
  (write-time item vectors, retrieval query vectors, chat recall, the
  demo server's MCP memory surface) rides the runtime's recorded
  `Context.embed`/`Runtime.embed` path whenever the runtime has an
  embedding provider — every embed emits
  `embedding.requested`/`embedding.responded` events and replays from
  the log with zero provider contact. Direct provider calls
  (`set_embedder`) remain supported for third parties and bare-graph
  hosts but are no longer used by first-party packs when a runtime
  records embeddings. Fixture `recorded_embedding_replay` proves the
  round-trip replays against a raise-on-contact provider. Details in
  the memory_gateway and chat changelogs.

- **Canonical `action_class` (R0–R4) across the gateway layer** (ADR
  0016; runtime CONTRACT v1.9). Capability specs, registrations,
  capability_call/approval/denial objects, the catalog, and pending
  approvals all carry the canonical consequence class as a SEPARATE
  dimension from the legacy `risk_class` — no mapping in either
  direction, anywhere. `decide_policy` evaluates two explicitly named
  dimensions (legacy `auto_approve_risk_classes` byte-for-byte as
  before; the action-class path ceiling-aware per ADR 0016: R4 →
  governance gate always, R3 → approval always, R0–R2 auto only at or
  below the effective ceiling, missing class fails closed).
  First-party capabilities are classified (reads R0; sends,
  create_reminder R3; mcp.set_exposure and both evolution adoption
  surfaces R4); MCP-discovered tools default to R3 (presumed outward)
  and gain a lower class only through explicit per-tool operator
  overrides. Runtime dependency pinned to the git ref carrying
  CONTRACT v1.9 (see pyproject.toml) until a release lands; manifests
  declare `>=1.9,<2.0` — correct the floor in
  scripts/generate_manifests.py if the contract ships under a
  different number. Per-pack details in the tool_gateway, mcp,
  schedule, telegram, whatsapp, and evolution changelogs.

- **L2 Habit closed loop.** Added a deterministic cross-pack fixture proving a
  normalizer-produced skill proposal, retry-safe exact-version usage, explicit
  helped outcome, skill reliability, reversible memory de-ranking, and two
  logged UTC interaction dates in the neutral usage vocabulary BabyAGI reads.

- **L2 Habit P3: canonical outcomes and artifact reliability.** Added mutually
  exclusive terminal outcomes, idempotent maintenance outcomes, explicit
  correction through supersession, and a separately queryable recency-aware
  reliability projection. Memory retrieval and skill eligibility consume the
  graph-visible reliability handoff reversibly; no player-facing value is read
  or written.

- **L2 Habit P4: governed skills.** Added immutable semantic versions with
  source provenance, idempotent exact-version usage, evaluation links,
  evidence-gated promotion, reversible eligibility, and capability-call
  routing. Reliability remains a separate artifact projection and never a
  score input.

- **L2 Habit D017: capabilities, not skills.** MCP and Tool Gateway now use
  capability terminology for executable surfaces. Skill remains reserved for
  the versioned, provenance-backed learned artifact; wire keys are unchanged.

- **Evolution pack v0.7.3: platform-truthful memory containment fixture.**
  Fixture 29 now follows the runtime's live memory-net signal: RLIMIT_AS
  contains the real Linux path, while the unforced macOS path proves the
  runaway is contained by the wall clock. Linux CI still forces the net-OFF
  path as a second pass, preserving coverage of both containment modes.

- **L1 Coverage P2: neutral usage and settlement projections.** Added the
  closed seven-category source model, provider-neutral connection surfaces,
  immutable named/versioned settling gates, normalizer-identity coverage,
  interaction and observed-outcome statistics, explicit lifecycle facts, and
  an explicit-event-horizon product query API. The default gate settles per
  surface at 25 unique identities or three UTC provider-time coverage days;
  fixtures remain visible but excluded. Added all seven P2 acceptance cases
  and source-zero dogfood using the vision repo plus a ChatGPT export across
  two settled categories.

- **L1 Coverage P1: activity normalization and historical importers.** Added
  the strict acquired-item/content contract, normalizer-owned logical evidence
  identity with idempotent revisions and supersession, content-addressed and
  reference-only replay modes, stable backfill cursors, deterministic
  versioned candidate extraction, and extractor invalidation. Added bounded
  `local_files` and official ChatGPT export adapters (including canonical tree
  paths plus abandoned edit/regeneration evidence), nested-pack manifest/CI
  support, and the full seven-case P1 acceptance suite.

- **Evolution pack v0.7.2: adoption-time supersession + narrow boot heal.**
  Resolves both crash-safety proposals from the v0.7.1 audit, per the
  owner's decisions. The per-pack invariant (at most one active promotion
  per pack name) is now maintained structurally on two fronts. (1)
  Adoption-time supersession: adopting a pack name with an existing active
  promotion disables the prior active and records it `superseded_by` the
  new one, as canonical-order step 6 (after the real promote), making the
  designed version-update flow correct. (2) Narrow boot heal that never
  guesses and always raises a `capability_gap`: a `loading` record whose
  `promote.applied` is in the log heals to active + loads + closes its
  ticket (fixes the serious window — live promoted state with the pack
  permanently unloaded); two actives supersede the older by recency; a
  `loading` record with no marker is parked. Plus a duplicate-`mod_rollback`
  idempotency guard. Fixtures 34/35 prove it; docs/evolution-design.md §10
  updated from proposal to decided-and-implemented.

- **Evolution pack v0.7.1: soak crash-safety + boot dedupe, plus a
  product crash-safety audit.** The Replit soak's rotation-15 red flag
  (two adopted packs simultaneously live) was fully diagnosed: real
  double-adoption, but the enabling bug was in the SOAK HARNESS after a
  mid-rotation container kill left it with a stale progress file. Fixed
  the harness completely (Gap A: persist the happy adoption the moment it
  commits, not at end-of-rotation; Gap B: the harness now asserts its own
  invariant — at most one active promotion total — and flips the digest
  RED on a violation instead of printing the count and passing) and the
  one unambiguous product bug (Gap C: `boot.py`'s `reload_adopted_packs`
  grouped nothing, so a pack could be loaded while reported disabled; now
  it groups by pack name, resolves by recency, loads once, and on two
  active promotions for one pack loads the most recent only with a loud
  log + `capability_gap`). Fixtures 31/32/33 prove each. Also a product
  crash-safety AUDIT (docs/evolution-design.md §10): a crash-window table
  over the two-phase adoption, disable, watch-monitor, and boot paths,
  with two findings left as PROPOSALS pending owner decision (per-pack
  adoption-time supersession; boot heal-vs-fail-closed on ambiguity) —
  investigate-and-propose only, no semantic change implemented.

- **Documentation and consistency audit: two code fixes plus doc
  reconciliation.** A report-only audit found two places where the code
  had drifted from documented intent, plus documentation staleness. Both
  code gaps are now closed and the docs reconciled to the shipped code
  (the code was treated as the source of truth throughout).
  - **C1 — Evolution pack v0.7.0: the stage-6 watch monitor is built.**
    `docs/evolution-design.md` §3 stage 6 described a post-adoption
    monitor that was never shipped (only three behaviors were
    registered). Now built: `watch_monitor` raises a reflection
    `capability_gap` when an adopted pack's own behavior fails within
    `watch_window_events` after its promote marker. Self-noticing, not
    self-healing. Fixture 30 covers in-window / out-of-window /
    non-adopted / dedup. Because the runtime suppresses `behavior.*`
    from behavior re-matching, the monitor scans the event log on
    ordinary activity rather than subscribing to `behavior.failed`; the
    design records that deviation.
  - **C2 — manifest-drift CI gate.** CI verified the content hash (which
    by design excludes `manifest.toml`) and the surface, but nothing
    checked the manifest-authored fields (the `activegraph` pin, python
    range, deps, provenance) against `scripts/generate_manifests.py`, so
    a generator/manifest pin drift stayed green. CI now regenerates every
    manifest and fails on any diff; proven against a deliberately
    introduced pin mismatch.
  - **Docs reconciled to the code.** Finished the vc-pack deletion (six
    tracked references in `README.md`, `packs/README.md`,
    `bundles/README.md`, and the overclaiming CHANGELOG line);
    `docs/evolution-design.md` synced (runtime floor `>=1.7.1`, §2 object
    types and status machine, §3 eleven-gate list and platform-conditional
    memory containment, §5 settings, §8 fixture list); `docs/manifest-spec.md`
    §3 corrected (capabilities are loader-verified since v1.4, per its own
    §9 Q8); `docs/llm-author-design.md` gate 4 stamped MET and the
    fixture-to-fold mapping corrected. Cosmetic: sample and `_template`
    pins bumped to `>=1.7.1`, illustrative "VC" mentions reworded.

- **Evolution pack v0.6.1: platform-aware runaway-memory containment.**
  The macOS soak was RED because budget_memory asserted the RLIMIT_AS
  memory net fires, true only on Linux; on macOS that net is
  deliberately OFF (Darwin cannot set address-space limits), so a fixed
  600MB allocation completed and the trial passed, breaking the
  assertion every rotation. Now budget_memory protects CONTAINMENT (true
  on both platforms) and keys off the runtime's own memory-net signal:
  the memory net contains a fixed over-cap allocation on Linux
  (unchanged, so the in-progress Linux run is unaffected), and an
  unbounded runaway is contained by the wall-clock kill on macOS. Also
  fixes the anomaly-attribution bleed (one path's child error rendering
  under another's). Fixture 29 covers both branches and the attribution
  fix; the runbook documents the platform-conditional outcome.
- **Evolution pack v0.6.0: the LLM author, MOCK model only.** Runtime
  floor `>=1.7.1,<2.0` (macOS RLIMIT_AS fix, memory-net-degrades-loudly,
  `activegraph.sandbox.preflight`, source-populated `TrialReport.detail`).
  `packs/evolution/author.py` builds the author from
  docs/llm-author-design.md: origin-classified frame assembly (four
  fixed sections, every excluded origin provably absent), a sealed
  drafting record with taint recomputed from admitted ids, a one-shot
  no-tools model call handed pure data, and pack-owned name/provenance
  (`agent_` prefix, the model returns four source bodies and touches
  neither name nor provenance). Rate caps: one draft in flight per gap,
  a daily cap, no redraft-from-rejection. Proven against a MOCK model
  with keyless fixtures 25-28 (origin assembly, the folds under the real
  author, taint-plus-caps, the gate-3 render). Author-build gates 1, 2,
  3, 6 MET; gate 4 fixtures on the real path; gate 5 (a green soak) is
  the remaining blocker. Live-model operation on a credentialed machine
  is gated on the soak's green finish, no substitutions. Keyless mock
  operation is what keeps this safe pre-soak.
- **Evolution pack v0.5.2: activegraph 1.7.0, soak green on the fixed
  trial child.** Runtime floor raised to `>=1.7,<2.0`. 1.7.0 computes
  the trial child's import path from the parent's resolved `sys.path`,
  so the child imports activegraph on any install the parent can
  (editable/venv/Nix) with the sandbox allow-list still closed. That
  answers the whitelist courier better than asked (automatic, pass
  nothing). Scope correction: the child-import break was not
  Replit-specific (a stock macOS venv failed identically on 1.6.0), so
  the prior "soak green on 1.6.0" was on the broken version and did not
  prove the fix. The soak preflight now delegates to the runtime's
  canonical `activegraph.sandbox.preflight`; the rolled-own probe is
  gone. **First honest soak on 1.7.0: one clean rotation, all seven
  paths OK, digest GREEN.** That is the green light that starts the real
  soak clock and clears the last author-build gate.
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
  and the CI step. (The doc references were swept later; see the
  documentation-audit entry above.) The bundle count drops to 4 and
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
