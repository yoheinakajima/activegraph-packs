# Evolution Pack: design

**Status: IMPLEMENTED (packs/evolution, activegraph >=1.7.1). This
document is the design of record; implementation-forced decisions were
folded back in the same commits that made them (per the house rule that
an uncovered decision is a doc bug). Runtime deliveries consumed: v1.4
promote with apply-time delta validation (CONTRACT v1.3 #4 addendum
4c), `disable_pack`, the manifest reference implementation; v1.5
subprocess trial isolation (`activegraph.sandbox.run_forked_trial`,
closing §7.2/T5's process edge) and enforced retention pins
(`activegraph.store.retention`, closing §7.5); v1.7/1.7.1 the trial
child's runtime-owned `preflight`, source-populated `TrialReport.detail`,
and the macOS RLIMIT_AS memory-net-off behavior the soak's budget_memory
path now keys off (§3 stage 3, threat T5).**

The evolution pack lets the assistant author new packs for itself, trial
them in isolation against its own history, and adopt them only after the
owner approves. Every step is graph state. The loop in one line:

```
capability_gap -> mod_proposal -> static gates -> fork trial -> owner
approval -> load_pack + promote (quiescent) -> active -> monitor ->
disable/rollback
```

The differentiating claim this pack has to earn: self-modification with
provenance. OpenClaw-class assistants write skills with no gates and no
audit trail. Hermes-class assistants have a learning loop you cannot
inspect or replay. This pack runs the same loop on an event-sourced
substrate where the trial is a fork you can diff, the adoption is a
marker event you can trace, and the rollback is a state you can
reconstruct.

---

## 1. Runtime primitives this design consumes

Verified working in activegraph 1.2.0, except `promote` and the trace DX
surface (`trace.events()` / `trace.failures()`), which both land in
v1.3.0 (promote per the amended promote design in promote-design.md):

| Primitive | Role in the loop |
|---|---|
| `rt.fork(at_event=...)` | Isolated trial environment with the parent's full history |
| `rt.load_pack(pack, settings=...)` | Hot-load the candidate inside the fork; later, the governed load on the parent |
| `rt.trace.events()` / `rt.trace.failures()` | Trial evidence: what fired, what broke, with tracebacks (v1.3.0 DX work) |
| `rt.diff(fork)` | The structural summary on the owner's decision surface |
| `parent_rt.promote(fork)` | Adopt the trial's net state delta, atomically, quiescently |
| `promote.applied` marker event | The single event behaviors may react to post-adoption |
| `activegraph.sandbox.run_forked_trial` (v1.5) | Stage 3's process boundary: candidate execution in a fresh-interpreter child, pin-verified, three nets |
| `activegraph.store.retention` `pins`/`retire` (v1.5) | §7.5: promoted-from fork logs refuse retirement; disposable trial forks archive at boot |

Consumption pattern for adoption, in order, inside one governed
executor. This is the single canonical order; §3 stage 5 restates it
with rationale:

1. Recompute gates against the proposal's pinned bundle hash.
2. `parent_rt.promote(fork_rt, dry_run=True)`. A conflict here aborts
   the adoption BEFORE anything irreversible happens (there is no
   unload), and the proposal moves to `conflict` with the runtime's
   conflict list attached. The retry path is the promote design's
   escape hatch: re-fork from the new tip, re-trial, re-propose.
3. `parent_rt.load_pack(candidate)` so the parent has the object types
   and behaviors the promoted state references. This is the governance
   seam the promote design deliberately left to the caller (§5 there).
4. `parent_rt.promote(fork_rt)`. Apply recomputes against parent-now;
   a conflict here (parent advanced since the dry run) still aborts
   atomically, and §3 stage 5 covers the resulting loaded-but-unadopted
   state.
5. React to `promote.applied` (quiescent apply means nothing else
   fires) and record the `mod_promotion`.

Apply-time delta validation (v1.4, CONTRACT v1.3 #4 addendum 4c) is
why step 3 loads the candidate BEFORE the real promote: promote
validates the full delta against the parent's REGISTERED schemas
pre-mutation, so with the candidate loaded, a schema-violating delta
raises `PackSchemaViolation` with nothing applied. Types no loaded pack
declares keep v0.9 untyped semantics (validated-or-untyped, never
silently unvalidated), so skipping the load does not fail, it just
validates nothing. The order is what buys the validation. Acceptance
fixture 13 proves both halves.

## 2. Graph vocabulary

New object types (all with full schemas at implementation time):

| Type | Purpose | Key fields |
|---|---|---|
| `capability_gap` | Something the assistant could not do | `kind` (tool_failure, unhandled_intent, reflection, owner_request), `description`, `evidence_refs`, `status` |
| `drafting_context` | What an author READ before it wrote (llm-author-design §4) | `charter_hash`, `gap_id`, `structured_fields`, `surface_sources`, `owner_input_ids`, `injection_flags`, `model`, `day` |
| `mod_proposal` | One candidate pack version | `gap_id`, `drafting_context_id`, `pack_name`, `pack_version`, `source_artifact_ids` (the manifest rides here as one artifact), `bundle_hash`, `rationale`, `authored_by`, `status` |
| `gate_result` | One gate's verdict on one proposal | `proposal_id`, `gate` (static, fixtures, in_sample, held_out), `verdict`, `details` |
| `mod_trial` | One fork trial of one proposal | `proposal_id`, `fork_run_id`, `forked_at_event`, `eval_summary`, `diff_summary`, `failures`, `verdict` |
| `mod_promotion` | A pack adoption, recorded at load time | `proposal_id`, `trial_id`, `pack_name`, `fork_run_id`, `promote_marker_event_id`, `applied_counts`, `bundle_hash`, `status` (loading, active, disabled) |
| `mod_rollback` | A disable/rollback action | `promotion_id`, `method`, `reason`, `at` |
| `adoption_ticket` | Phase-one output of a governed adopt/disable call, applied by the chassis between frames (§3 stage 5) | `kind` (adopt, disable), `proposal_id`, `promotion_id`, `call_id`, `status` |

Proposal source files are stored as Core `artifact` objects, one per
file, hashed individually; `mod_proposal.bundle_hash` is the BUNDLE
hash: the manifest-spec §4 walk WITHOUT the manifest exclusion, so it
covers every byte including `manifest.toml` itself. The manifest is the
exact document the owner's decision surface renders (risk classes,
`consumes`, `authored_by`), so a pin that excluded it would leave
approve-then-swap of the manifest open. Computed by
`activegraph.packs.manifest.compute_bundle_hash`, imported, never
reimplemented; recomputed at gate time, at adoption time, and at every
boot re-materialization. The hash is the pin: what the owner approved
is byte-identical to what the executor loads, or the executor
refuses.

Proposal status machine:

```
drafted -> gated -> trialed -> pending_approval -> adopting -> promoted
                \-> rejected (gate fail)      \-> denied
trialed -> conflict (promote aborted) -> re-trial or abandoned
conflict -> needs_owner (retry cap reached, terminal; see §3 stage 5)
promoted -> disabled (rollback)
any -> suspended (injection taint, see §6)
```

(`adopting` is set by the phase-one executor when it queues the
adoption ticket; `needs_owner` is the terminal park the retry-capped
chassis moves a repeatedly-conflicting proposal to. The
`mod_promotion` is born `loading` at load time and flips to `active`
on the `promote.applied` marker.)

## 3. Lifecycle stages

### Stage 0: gap detection

Deterministic behaviors, no LLM required to notice a gap:

- `capability_result` with `success=False` for the same capability N
  times in a window (N configurable) opens a `tool_failure` gap.
- A `comm_intent` no behavior claims opens an `unhandled_intent` gap.
- A schedule-pack reflection tick can open `reflection` gaps from an LLM
  review of recent failures (this one uses the model, and its output is
  a gap description, never code). Taint inheritance is deterministic and
  does NOT trust the model: a reflection gap's `injection_flags` are the
  UNION of the flags on every `capability_result` the review consumed,
  computed by the behavior from the review's input set, regardless of
  what the model wrote into `evidence_refs`. An LLM sitting between a
  flagged result and a fresh gap must not be able to launder the taint
  away (§6 T2).
- The owner can just ask; chat routes an explicit request to an
  `owner_request` gap.

### Stage 1: drafting

A candidate is a manifest (per manifest-spec) plus source files,
submitted through `submit_proposal_fn`. The AUTHOR is pluggable behind
that one entry point: the acceptance fixtures use a scripted generator,
a chat tool can route an owner-drafted pack, and the LLM author (an
`@llm_behavior` drafting from a `capability_gap`) is host wiring that
arrives with the product chassis, because prompt design for code
generation is a product concern, never gate machinery. Every author
goes through the same gates. Constraints on what may be authored:

- **Both manifest and source, always together.** The manifest declares
  the full capability surface; the static gate cross-checks source
  against declaration. An undeclared behavior in the source is a gate
  failure, so the declaration is load-bearing, never decorative.
- **Fixed file set**: `manifest.toml`, `__init__.py`, `object_types.py`,
  `behaviors.py`, `tools.py`, `settings.py`, `fixtures/run_fixtures.py`.
  This is `packs/_template`'s structure minus the human-facing docs
  (`README.md`, `CHANGELOG.md` stay a human convention; the proposal's
  rationale field serves that role for agent packs). No other modules,
  no packaging files, no scripts. Fixtures are mandatory: an
  agent-authored pack meets the same bar as a human-authored one, and
  the template ships a fixtures skeleton so "follow the template" and
  "ship fixtures" are the same instruction.
- **New packs and updates to its own prior packs only.** Modifying
  human-authored packs is out of scope for v1; the agent proposes a new
  version of an agent-authored pack as a fresh proposal with a version
  bump.
- Drafts execute nothing. A proposal is data until stage 3.

### Stage 2: static gates (deterministic, zero LLM, zero execution)

Run in order, first failure stops the pipeline, every verdict is a
`gate_result`. Three structural pre-gates run BEFORE the numbered eight
(the numbering below stays stable across doc revisions; the code names
them `static:reserved_paths`, `static:file_set`, `static:trial_driver`,
so `run_static_gates` emits eleven gate names in total):

- **Reserved paths** (`static:reserved_paths`): the proposal may not
  author any human-PR-only file — first and foremost the author charter
  (llm-author-design §3a/§8). Refused before any other gate runs.
- **Fixed file set** (`static:file_set`): every authored file must be in
  the allowed set (manifest, `__init__`, `object_types`, `behaviors`,
  `tools`, `settings`, the two fixture files); nothing else is admitted.
- **Trial driver** (`static:trial_driver`): `fixtures/trial_scenario.py`
  is the chassis driver, included verbatim and gate-verified byte for
  byte (design §3 stage 3).

1. **Manifest validity**: parses, schema-valid per manifest-spec,
   version sane, declared runtime range includes the pinned runtime.
2. **Hash integrity**: the manifest's internal content hash matches
   the stored source artifacts (manifest-spec §4), and the proposal's
   BUNDLE hash matches the full artifact set including the manifest.
3. **Declared-vs-actual**: AST walk of the source; every behavior,
   tool, object type, and capability registration found in code must be
   declared in the manifest, and vice versa.
4. **Import allow-list**: stdlib modules from a short list (`typing`,
   `datetime`, `json`, `re`, `math`, `dataclasses`), `pydantic`,
   `activegraph.packs`, and intra-pack relative imports. Everything
   else fails the gate. In particular: no `socket`, `urllib`,
   `requests`, `subprocess`, `os`, `sys`, `pathlib`, `importlib`.
   External effects go through gateway capabilities or they do not
   happen. Exemption: `fixtures/run_fixtures.py` is test harness, never
   loaded runtime code, and legitimately needs `sys`/`pathlib`; it gets
   a fixtures-specific allow-list (the standard list plus `sys`,
   `pathlib`) and is governed primarily by the manifest's
   `deterministic = true` assertion (no sockets, no key reads),
   enforced when the fixtures run inside the fork.
5. **Banned constructs**: `exec`, `eval`, `compile`, `__import__`,
   dunder attribute access on strings, `getattr` with computed names.
6. **Reserved namespaces**: the pack may not declare capabilities,
   tools, or behaviors whose names collide with `tool_gateway.*`,
   `evolution.*`, `mcp.set_exposure`, or anything in
   `tool_gateway.untrusted.NEVER_LLM_CALLABLE`. A pack that tries to
   register `approve_capability` under any provider name fails here,
   and would fail again at `as_llm_tool` (belt and braces, task #6).
7. **Size caps**: total source bytes and per-file caps (configurable,
   default small). The cap is a review-surface guarantee: the owner
   must be able to actually read what they approve.
8. **Injection scan**: task #6 patterns over all source and prompt
   text. A hit does not auto-reject; it suspends (§6).

Honesty note: static analysis of Python is a filter, never a sandbox.
Gate 4 and 5 raise the cost of casual badness and catch the model's
mistakes; they do not stop a determined adversary who controls the
model. The containment that holds is §6's structure: nothing loads
without a verified owner approval of the exact reviewed bytes.

### Stage 3: fork trial (subprocess, v1.5)

```python
report = run_forked_trial(              # activegraph.sandbox
    store_path, parent_run_id=rt.run_id, at_event=parent_tip,
    pack_source=PackSource(root, expected_bundle_hash=proposal_pin),
    scenario="fixtures/trial_scenario.py::main", limits=TrialLimits(...))
fork = Runtime.load(store_path, run_id=report.fork_run_id, behaviors=[])
```

The parent forks; the child is a fresh interpreter that verifies the
bundle-hash pin BEFORE importing anything, loads the fork by run id,
and runs the scenario under the runtime's three nets (rlimits,
parent-side wall-clock kill, event budget). Platform-conditional net,
folded back from the soak: the RLIMIT_AS memory net is live on Linux
but OFF on macOS (Darwin refuses address-space limits, and v1.7.1
degrades it loudly rather than silently). The soak's budget_memory path
keys off the runtime's own net-availability signal (not `sys.platform`)
and asserts CONTAINMENT either way — the memory net catches a fixed
over-cap allocation on Linux, the wall-clock kill catches an unbounded
runaway on macOS. A memory-net-OFF warning on macOS is expected; the
only red flag is a runaway that completes. The parent never imports
candidate code at trial time. Two child runs per trial: the
candidate's own fixtures (the sandbox smoke), then the chassis trial
driver. The driver is an interface-forced design decision folded back
per the house rule: `run_forked_trial` requires the scenario file to
live inside the bundle-hashed pack root, so
`fixtures/trial_scenario.py` joined the authored file set. Authors
include the canonical render verbatim (`trial_driver.py`), gate 0b
refuses any proposal whose copy differs byte for byte, and the
held-out split is thereby decided at proposal creation and frozen
under the same pin the owner approves, which is what this design
wanted the split to do anyway. The store is the record: the driver
leaves untyped stage markers in the fork; the parent reads them,
records gate_result objects, and removes the markers so the promote
delta stays clean.

Scenario discipline, stolen from the regimes repo (static -> sandbox ->
in-sample -> held-out):

- **Fixture gate (sandbox)**: the proposal's own
  `fixtures/run_fixtures.py` runs inside the fork and must pass. This
  is the cheap smoke: does the pack even work as claimed.
- **In-sample replay**: re-inject a segment of the parent's recorded
  inputs (sources, chat inputs; recorded, deterministic, no live LLM
  when the parent ran in mock or when cached responses exist) and
  `run_until_idle`. The drafting behavior saw this segment when it
  authored; passing here is necessary, never sufficient.
- **Held-out replay**: a disjoint history segment the drafting stage
  never saw, selected when the proposal is created and touched exactly
  once, at this gate. Regimes' OPTIMIZE/CONFIRM rule: reporting an
  in-sample number as the result is overfitting.
- **Trial budget**: hard caps on events, LLM calls, and wall time
  inside the fork. A runaway candidate fails the trial; the fork is
  discarded; the parent never noticed.

Pass criteria (all): fixtures green, zero `trace.failures()` from
candidate behaviors, no injection flags raised inside the trial, replay
scenarios produce no regressions in existing packs' fixtures run inside
the same fork, budget respected. `mod_trial.verdict` records it; the
fork run id is kept for audit and for the promote step.

A passing fork then sweeps its own bench (§7.3), inside the child (the
trial driver owns the sweep): every object and relation CREATED in the
fork after the candidate loaded (the replayed input copies and
everything derived from them) is removed before the fork is handed to
adoption, so the promote delta carries measurements out and adoptable
state only. Patches to pre-existing shared objects
stay, deliberately: they are the candidate's claim about desired state,
and exactly what the conflict check and the decision surface's diff
counts exist to scrutinize. `mod_trial.eval_summary` records the sweep
counts (`replay_residue_removed`).

Open problem, stated instead of designed around: input re-injection
needs a convention per channel (what exactly gets replayed for a chat
segment vs an email segment), and "no regressions" needs a comparator
for graph state beyond fixtures. v1 scopes the replay gates to
fixture-defined scenarios plus recorded chat segments, and the
comparator to `trace.failures()` plus fixture assertions. Richer
behavioral evals (LLM judges, regimes-style regime taxonomies) are
explicitly later.

### Stage 4: the owner's decision

Adoption is a gateway capability, `evolution.adopt_proposal`, registered
at risk class `critical`, which no sane `auto_approve_risk_classes`
includes; the pack refuses to register it if the active gateway settings
would auto-approve it. The model can propose adoption; only a verified
approver can resolve the hold.

"Verified" has to actually mean something here, and by default it would
not: the gateway's approver check degrades gracefully when identity_auth
is absent or has no registered principals, accepting any approver_ref as
`identity_unverified`. That is the right posture for a demo capability
and the wrong one for self-modification. So the evolution pack requires
identity: registration of `evolution.adopt_proposal` refuses (same
pattern as the auto-approve refusal above) unless identity_auth is
loaded AND at least one principal with an approver role is registered,
and the acceptance fixtures assert both refusals. Unverified-mode
adoption does not exist.

What the owner sees on the decision surface (all already graph state):
the gap, the rationale, the full source diff (small by gate 7), the
manifest's declared surface INCLUDING `consumes` (which existing
capabilities this pack will invoke, i.e. its outbound reach), gate
verdicts, trial summary with failures and eval numbers, `rt.diff`
counts, the fork run id, and any flags. Approval is
`approve_capability` on the held call, nothing bespoke.

Implemented: `packs/evolution/review.py`. `build_review` assembles that
model from graph state alone and `render_review_html` renders the one
page, with the author banner first (AUTHORED BY: AGENT, loudly), taint
banners above the fold, per-file unified diffs against the currently
adopted version of the same pack (a first adoption diffs against
nothing, so every line renders as added), and the approve/deny controls
bound to the held call. The demo server serves it at
`/approvals/review?proposal_id=...`; `/approvals` itself
content-negotiates, JSON for API clients and the review index for
browsers, so the owner's path of least resistance is the readable page
and never a raw JSON blob. Agent-authored text is escaped everywhere it
appears. Fixture 14 renders a real proposal end to end and asserts
every section is on the page.

The decision CHANNEL is authenticated separately from the decision
itself: the demo server requires a bearer token
(`ACTIVEGRAPH_APPROVAL_TOKEN`, constant-time compare, 401 without it,
refusals audited as approval_auth_denial objects) on approval posts,
and refuses decisions outright when evolution is on with no token
configured. The principal check on the approver ref remains the
decision underneath. Stated plainly: this is demo-server hardening of
the transport; binding an HTTP session to a verified principal is the
product chassis's job, and this token does not pretend to be that.

### Stage 5: adoption (the governed executor, two-phase)

Implementation-forced decision, folded back per the house rule: the
gateway executes approved calls INSIDE a behavior frame
(`call_executor`), and `promote` / `load_pack` / `disable_pack` mutate
runtime registries and apply events, which must not happen mid-frame.
So adoption is two-phase. Phase one is the governed part: the
`evolution.adopt_proposal` executor (reachable only through an approved
capability call) validates the proposal reference and writes an
`adoption_ticket`. Phase two is the chassis part:
`process_adoption_tickets(rt)`, called by the host between
`run_until_idle` cycles (the demo server's runtime-executor loop, or a
fixture), performs the canonical order below OUTSIDE any frame. The
governance is unchanged: tickets are born only from approved calls,
every phase-two step is graph state, and a crashed phase two leaves the
ticket visible.

Phase two, in the §1 canonical order:

1. Recompute the BUNDLE hash of the stored artifacts (manifest
   included, per §2) and compare to the approved proposal's pin.
   Mismatch aborts loudly. This closes the approve-then-swap window
   for code and manifest alike: what loads is what was reviewed.
2. Re-run the static gates (cheap, deterministic; the world may have
   changed, e.g. gateway settings now auto-approve critical).
3. `parent_rt.promote(fork_rt, dry_run=True)`. A conflict aborts NOW,
   before the irreversible `load_pack` (there is no unload), leaving
   the parent completely untouched. The proposal moves to `conflict`.
4. Materialize the pack from artifacts and `parent_rt.load_pack(...)`,
   AND record the `mod_promotion` immediately, in status `loading`.
   Recording before the real promote matters: from this moment the
   candidate's behaviors are live on parent events, and §6's disable
   and monitoring machinery key off `mod_promotion`, so the pack must
   be trackable even if step 5 aborts.
5. `parent_rt.promote(fork_rt)`. The real apply recomputes against
   parent-now and can still conflict if the parent advanced since step
   3. That abort leaves the pack loaded and the state unadopted:
   `mod_promotion` stays at `loading` (trackable, disable-able, and
   excluded from boot re-load until resolved), the proposal moves to
   `conflict`, and the retry is re-fork with the pack already present.
   The single-writer chassis (the demo server's runtime executor
   thread) makes this window small; the design does not pretend it is
   zero.
6. On `promote.applied`, an evolution behavior patches `mod_promotion`
   to `active` (marker event id, from_run, counts) and the proposal to
   `promoted`.

Quiescent apply is load-bearing here: the fork already ran every
cascade the delta implies, so the parent adopting state must fire
nothing per-entity. The evolution pack reacts to the one marker event
and that is sufficient by design.

The conflict outcomes (steps 3 and 5, plus a trial fork lost to a
restart) feed a BOUNDED automatic retry (`packs/evolution/chassis.py`,
`sweep_evolution`, the function hosts should call instead of raw
`process_adoption_tickets`). A conflict is a timing problem, so the
chassis may re-gate, re-trial at parent-now, and requeue a ticket under
the SAME approved call: the approval authorized exact bytes pinned by
bundle hash, and none of that changed. But a proposal that keeps
conflicting is telling you its target state is contested, and an
uncapped chassis would fork, replay, and requeue forever. After
`max_conflict_retries` automatic attempts the proposal moves to
`needs_owner`, a TERMINAL state: gates, trials, and ticket processing
all refuse it (a hand-opened ticket aborts without touching it), the
/approvals index lists it under "needs owner action", and only the
owner moves it again. Fixture 16 walks the full park-and-refuse path.

### Stage 6: monitor, disable, roll back

- **Watch window**: after adoption, the `watch_monitor` behavior looks
  for `behavior.failed` events from the candidate's own behaviors within
  `watch_window_events` events after its promote marker, and raises a
  `capability_gap` of kind `reflection` if the failure rate is nonzero.
  Self-noticing, not self-healing: fixes are new proposals through the
  same loop. Implementation detail folded back from the build: the
  runtime deliberately suppresses `behavior.*` events from behavior
  re-matching (loop prevention), so `watch_monitor` cannot subscribe to
  `behavior.failed` directly. It reacts to ordinary graph activity
  (`object.created`) and scans the event log instead, keyed off the
  behavior names each `mod_promotion` records at load time, so only an
  adopted pack's own failures — never unrelated ones — trigger it.
  Acceptance fixture 30 proves in-window / out-of-window / non-adopted /
  dedup. Because event ids reset on restart, the acute watch window is
  a within-run notion; a marker from a prior run yields no match, which
  is the honest post-restart posture.
- **Disable (v1 rollback, upgraded for v1.4)**: `mod_promotion.status
  = disabled` via a governed capability, PLUS immediate deregistration:
  `rt.disable_pack(name)` removes the pack's behaviors, tools, and
  validators from the live registries, so nothing fires from the moment
  of disable (code objects stay in memory, inert; true unload of
  imported Python is not honestly achievable and the runtime does not
  claim it). Boot-time exclusion via `mod_promotion` records remains
  the durable half. Re-enable is a fresh adoption.
- **State rollback**: promoted state is ordinary events; history is
  immutable. Full undo exists structurally (fork the parent at the
  event before the promote marker) but re-homing a fork as the new
  primary is a chassis operation this pack does not attempt in v1.
  Practical v1 posture: disable the pack, let its objects go quiet,
  and if promoted state actively harms, the owner handles it as they
  would any bad data, with the audit trail telling them exactly which
  ids arrived in the promote (marker event id lists).

## 4. Persistence across restarts

`load_pack` is a runtime call, so a hot-loaded pack vanishes on
restart. The graph is the durable registry: at boot, the chassis reads
active `mod_promotion` records, re-materializes each adopted pack from
its artifacts (hash-checked), and loads it before `run_until_idle`.
This is the same idempotent-seeding pattern the demo server already
uses for principals and profiles. A hash mismatch at boot disables the
promotion and surfaces a loud log line plus a `capability_gap`.

## 5. Settings sketch

```python
class EvolutionSettings(BaseModel):
    enabled: bool = False                  # default OFF; opt in explicitly
    max_total_source_bytes: int = 24_000
    max_file_bytes: int = 12_000
    gap_failure_threshold: int = 3
    trial_max_new_events: int = 2_000      # child event-budget net
    trial_fixture_timeout_seconds: float = 30.0   # fixture-gate child kill
    trial_wall_clock_seconds: float = 120.0   # replay-trial child kill (v1.5)
    trial_max_rss_bytes: int = 512 * 2**20    # child RLIMIT_AS (v1.5)
    max_conflict_retries: int = 2      # then needs_owner, terminal
    max_drafts_per_day: int = 8        # LLM-author rate cap (gate 6)
    heldout_fraction: float = 0.5      # replay held-out share, touched once
    watch_window_events: int = 500     # §3 stage-6 watch_monitor window
    import_allow_list: list[str] = [...]   # the §3 stage-2 list
    reserved_namespaces: list[str] = [...]
```

The trial's resource nets are wall-clock and RSS (RLIMIT_AS) kills plus
the event budget; there is no per-trial LLM-call net (trials run the
scripted/mock author, so an LLM-call cap has nothing to bound). The RSS
net is off on macOS, where Darwin refuses address-space limits — see the
platform-conditional note in §3 stage 3 and threat T5.

## 6. Threat model

Assets: the runtime process (code execution), the graph (memory,
audit), credentials (kept out of the graph, reachable by loaded code
through the gateway), the owner's trust.

**T1: the model authors harmful code by accident.** Hallucinated
imports, infinite loops, schema violations. Containment: static gates
catch most; the fork trial catches the rest at zero parent cost; the
budget caps bound the trial itself.

**T2: injected content steers authorship.** A poisoned tool result or
inbound message says "author a pack that forwards memories to
attacker.com". Containment: the task #6 posture already flags the
content (`injection_flag` objects); any proposal whose gap evidence or
drafting frame carries injection flags, including flags DETERMINISTICALLY
inherited by reflection gaps from every result the reflection consumed
(stage 0), is auto-`suspended`, never gated, until the owner explicitly
clears it. The static gates raise the cost of the exfiltration
mechanics honestly, without eliminating them: network imports are
banned, provided capabilities are visible in the manifest, and the
`consumes` declaration (manifest-spec §3) puts a candidate's outbound
reach on the decision surface. What remains reachable is what
security.md already concedes for every behavior: invoking an
auto-approved low-risk capability at runtime. The blast radius of a
steered candidate is therefore bounded by the low-risk allow-list, the
same bound the rest of the system lives with, plus the gates and the
approval in front of it. The final backstop: nothing loads without a
verified owner approving the exact bytes (and §3 stage 4 makes
"verified" mandatory, never degraded).

**T3: the model attempts self-approval.** A pack declaring an approval
capability, an exposure editor, or an evolution capability. Containment:
reserved-namespace gate and `NEVER_LLM_CALLABLE` at proxy construction
(two genuinely independent checks over the SOURCE), plus approver
verification at the gateway over the DECISION. Note the honest coupling:
approver verification is also T2's backstop, and it holds only because
stage 4 refuses to register adoption in unverified-identity mode. That
refusal is load-bearing for both threats and gets its own fixtures.

**T4: approve-then-swap.** Source OR MANIFEST mutates between owner
approval and executor load. The manifest is an attack surface in its
own right: swapping only `manifest.toml` relabels risk classes, empties
`consumes`, or flips `authored_by` while the reviewed code stays
untouched. Containment: the proposal pins the BUNDLE hash (§2), which
covers every byte including the manifest, recomputed at gate time, at
adoption, and at every boot re-materialization via the runtime's
`compute_bundle_hash`.

**T5: the trial itself as attack surface.** Candidate code runs inside
the fork during stage 3, before any approval. The v1.5 runtime softened
this edge from "static gates are the only wall" to "static gates
filter, the process boundary contains": ALL candidate execution now
happens in the runtime's trial child (`activegraph.sandbox`), a fresh
interpreter with an allow-list environment (no API keys by
construction), rlimits, a parent-side wall-clock kill, and the event
budget as a third net. The parent never imports candidate code at
trial time; the import happens at adoption, after gates re-run and a
verified approval. Fixture 17 proves the containment: a candidate that
spins forever at module import used to be able to hang the parent
runtime at materialization, and now dies in the child at the wall
clock while the parent records the rejection. The honest limits are
the runtime's, restated: this is crash and state isolation, never a
security sandbox. The child can still open sockets or read the
filesystem; syscall and network confinement remain host territory
(containers, seccomp), the import allow-list and banned-constructs
gates remain the pre-execution filter for exactly that reason, and a
verified owner reading the full diff remains the containment for a
malicious candidate that behaves politely in the trial.

**T6: gate erosion.** Future contributors relax a gate default or
auto-approve `critical`. Containment: registration-time refusal (§3
stage 4), fixtures that assert the refusal, and `enabled: False` as the
shipped default.

## 7. Explicit open problems

1. **RESOLVED in v1.4: `disable_pack`.** Deregistration is live
   (behaviors stop firing immediately); memory eviction still needs a
   restart, which the runtime states plainly and this design accepts.
2. **RESOLVED in v1.5: trial process isolation.** Stage 3 runs on the
   runtime's `run_forked_trial`: fixture gate, in-sample replay, and
   held-out replay all execute in a fresh-interpreter child that
   verifies the bundle-hash pin before importing anything. The fork
   persists in the store, so a restart between trial and adoption no
   longer forces a re-trial (adoption reloads the fork by run id). One
   scoping consequence, stated plainly: the child loads ONLY the
   candidate pack, so replay exercises the candidate against recorded
   inputs in isolation from other packs' behaviors. The v1 comparator
   was candidate-only failures anyway, and candidate-only isolation
   stays the canonical default. Cross-pack interaction trials are now
   available should we want them: the runtime added
   `run_forked_trial(..., extra_packs=(PackSource(...),))` in v1.7,
   each extra pack through the identical pin chain as the candidate.
   The pack does not use `extra_packs` yet (the floor is `>=1.7.1` for
   the preflight/memory-net-signal work the soak consumes, not for this),
   and the pinned-driver plus marker-sweep patterns this pack uses are the
   documented recommendation in the runtime's trial-isolation-design
   §2b, with the CONTRACT #4d residue linkage made explicit there.
3. **RESOLVED in v0.2: trial replay residue.** Promote's three-way
   diff treats every fork-only create as adoptable state, so the
   replayed input copies (and everything the candidate derived from
   them) would ride the delta into the parent as duplicate history.
   Policy: the trial sweeps its own bench. After a passing trial,
   every object and relation created in the fork after the candidate
   loaded is removed from the fork before adoption sees it; the parent
   already lived those inputs once, and their replayed derivatives are
   measurements, not adoptable state. Patches to pre-existing shared
   objects stay in the delta deliberately (the candidate's reviewed
   claim about desired state, and the conflict check's whole subject).
   The two rejected alternatives, for the record: scratch-store trials
   break promote's same-store lineage requirement, and mark-and-sweep
   in the parent pollutes the parent log with create-remove pairs that
   promote never needed to apply. Still open from the old item: per
   channel replay conventions beyond chat segments.
4. **Behavioral regression depth.** v1's comparator is failures plus
   fixture assertions. Graph-state diffing against expected shapes is
   future work.
5. **RESOLVED in v1.5: fork log retention.** The runtime's retention
   API enforces the pin this design asked for: `pins()` lists why a
   run cannot be compacted or retired, promoted-from fork logs head
   the pin set, and `retire`/`compact` refuse with
   `RetentionPinnedError`. The pack consumes it in
   `boot.retire_unpinned_trial_forks`: disposable trial forks (
   rejected candidates, fixture-gate runs) are archived at boot,
   in-flight forks are kept by proposal status, and promoted-from
   forks are refused by the runtime itself, which fixture 18 asserts.
   The pin set dominates any policy here, unconditionally.

   Concurrency ruling (CONTRACT v1.5 #2 addendum 2b): the offline
   requirement is per-RUN, so retiring fork runs is sanctioned while a
   live runtime is attached to OTHER runs in the same store (the
   runtime pins the shape with
   test_retire_fork_per_run_while_parent_runtime_is_live). Two
   conditions the pack keeps: never race a pin-creating operation
   against retirement of the same run (retire only after decisions are
   final, which the terminal-status kept set guarantees; a lost race
   degrades an audit walk but destroys nothing, since archived rows
   stay readable via `iter_archived`), and never compact or retire a
   run under a runtime attached to that same run (the snapshot-event
   id collision, the one real hazard). The demo server's pre-boot
   placement honors the second condition by construction; fixture 18
   proves the first-condition-safe per-run case with the parent
   runtime live throughout.
6. **Re-homing.** Full undo by forking pre-promote exists structurally;
   making that fork the primary run is unowned. Out of scope v1.
7. **Concurrent authorship.** One proposal in flight per pack name at
   a time in v1; a queue is trivial later, a merge is not.

## 8. Acceptance fixtures (the bar before BabyAGI wires this in)

All deterministic, no API keys, no network; the author in fixtures is a
scripted generator, never a live LLM:

1. **Happy path**: gap -> proposal -> gates pass -> fork trial passes ->
   held call -> owner approves -> load + promote -> `mod_promotion`
   recorded -> the candidate behavior fires on the next matching parent
   event.
2. **Static gate matrix**: banned import, banned construct, undeclared
   behavior, reserved namespace, oversize file, manifest/hash mismatch.
   Each produces the right `gate_result` and a `rejected` proposal.
3. **Trial failure**: candidate behavior raises inside the fork; the
   trial records the traceback, verdict fail, parent state untouched
   (assert zero new parent events).
4. **Held-out discipline**: a candidate that passes in-sample and fails
   held-out is rejected, and the held-out segment is provably touched
   exactly once (event-count assertion on the trial).
5. **Conflict and retry**: parent advances between dry_run and adopt;
   promote aborts; proposal shows `conflict` with the runtime's
   conflict list; re-fork retry succeeds.
6. **Injection taint** (also covers the fixture-11 case): a gap whose
   evidence carries injection flags yields a `suspended` proposal that
   no gate will touch until cleared, and the inheritance is deterministic
   even when the scripted reviewer omits the flagged source from
   `evidence_refs`.
7. **Self-approval attempt**: a generated pack declaring
   `approve_capability` fails the reserved-namespace gate; forcing it
   into the registry anyway is refused by `as_llm_tool`.
8. **Hash pin**: artifact bytes mutate after approval; the adoption
   executor aborts; nothing loads. Second case: ONLY `manifest.toml`
   mutates (a relabeled risk class); the bundle-hash pin still aborts.
9. **Restart persistence**: adopted pack reloads at boot from
   `mod_promotion`; a disabled one stays down; a hash-mismatched one is
   disabled loudly.
10. **Registration refusals**: gateway settings that would auto-approve
    `critical` make `evolution.adopt_proposal` registration fail, and
    so does a runtime without identity_auth loaded or without a
    registered approver-role principal (unverified-mode adoption must
    not exist).
11. **Taint inheritance** (folded into fixture 6, no standalone fixture —
    the code's `SCENARIOS` skips 11): a reflection review that consumed a
    flagged capability_result produces a gap carrying the inherited flags
    even when the (scripted) reviewer omits the flagged source from
    evidence_refs; the resulting proposal is `suspended`. `fx_06`
    asserts exactly this, so the number is kept as a pointer and every
    "fixture N" reference below still names the matching `fx_N`.
12. **Loading-state tracking**: a real-promote conflict after
    `load_pack` leaves `mod_promotion` at `loading`; disable works on
    it, and a restart does not re-load it.
13. **Apply-time validation and load order**: with the candidate loaded
    on the parent, a schema-violating delta raises
    `PackSchemaViolation` with zero mutation; without `load_pack` the
    same delta promotes untyped. The canonical order is what buys the
    validation.
14. **Decision surface**: the review page renders a real proposal end
    to end from graph state alone (author banner, full diff, surface
    including consumes, gates, trial numbers, fork run id, held call),
    a tainted proposal renders its flags loudly with no approve
    control, and the /approvals index links the held adoption to its
    review page.
15. **Trial residue**: after a full adopt, the parent holds exactly its
    original recorded inputs, zero replay-derived copies or outputs,
    the shared-state patch still promotes, and the trial records what
    it swept.
16. **Retry cap**: a persistently conflicting adoption is retried
    exactly `max_conflict_retries` times, then parked at `needs_owner`;
    idle sweeps do nothing further and a hand-opened ticket is refused
    without touching the parked status.
17. **Subprocess isolation** (fixture 3's twin, v1.5): a candidate that
    spins forever at module import passes every static gate, dies in
    the trial child at the wall clock (`limits_exceeded`), and the
    parent stays alive, records the rejection, and keeps processing
    events.
18. **Retention pins**: after a full adopt, retiring the promoted-from
    fork raises `RetentionPinnedError` with the promoted-from reason; a
    rejected candidate's forks retire clean; the boot housekeeping
    helper makes the same calls and reports every decision. Also proves
    the sanctioned per-run concurrency (addendum 2b): the parent
    runtime stays live on the store throughout while the retention
    calls operate on the fork runs.
19. **Soak rotation** (gate 5's harness, docs/soak-runbook.md): one
    full rotation reaches every expected terminal state on a fresh
    keyless store (happy, conflict-park, disable-restart, all three
    budget nets, tainted-suspended), the digest reads GREEN, and the
    state file counts every path.
20. **Drafting record** (gate 3 pulled forward): the drafting_context
    record renders as its own review section; a nonzero taint union
    suspends the proposal deterministically at submission, shows the
    loud banner, and offers no approve button; a referenced-but-absent
    record renders as a refusal.
21. **Charter integrity** (llm-author-design §3a/§8, review change 1):
    a proposal whose file set targets the reserved charter path is
    refused at `static:reserved_paths`, before any other gate runs.
22. **Drafting taint recompute** (llm-author-design §4, review change
    2): the taint union is recomputed from the record's admitted object
    ids at submission, so a record that lies about its stored flags
    still suspends.
23. **Structured-field charset** (llm-author-design §3b/§6, review
    change 4): a prose-shaped structured field, and a field path off the
    §3b allow-list, are both refused at submission.
24. **Soak preflight + crash detail**: an incapable box is refused
    before rotation 1; a trial-child failure is never opaque in the
    digest.
25. **Author origin assembly** (llm-author-design §3): the frame is four
    fixed sections and nothing else; a planted memory, profile goal,
    tool output, prior rationale, and the exception message all provably
    never reach it; it admits exactly the §3 set.
26. **Author pipeline and folds**: the MOCK author produces a real
    proposal with a pack-owned `agent_` name and provenance, no tools in
    the frame, the charter unauthored, the charset fold at assembly, and
    the authored pack passes a real subprocess trial.
27. **Author taint and caps**: a tainted context suspends even when the
    mock output is pristine; the one-in-flight, daily, and no-redraft
    caps refuse.
28. **Author render (gate 3)**: a MOCK-LLM-authored proposal renders end
    to end on the decision surface, what it read beside what it wrote.
29. **budget_memory platform-aware**: the soak's runaway-memory path is
    contained on both platforms, keyed off the runtime's real
    memory-net signal — the memory net catches a fixed over-cap
    allocation on Linux (`materialization_failed`), the wall-clock kill
    catches an unbounded runaway on macOS (`limits_exceeded`) — and the
    anomaly attribution reads its own trial's child detail, never a
    neighbouring path's.
30. **Watch monitor** (§3 stage 6): an adopted pack whose own behavior
    fails within the watch window produces exactly one reflection
    `capability_gap`; a non-adopted behavior's failure, and a failure
    past the window, produce none; a second in-window failure does not
    stack a second open gap.

## 9. Dependencies and sequencing

Blocked by: manifest spec (#4) locked and consumed; runtime v1.3.0 with
promote and the DX items (#10). Consumes: task #6 posture
(`injection_flag`, `NEVER_LLM_CALLABLE`), tool_gateway approval
machinery, identity_auth approver verification, artifacts (core),
schedule (reflection ticks). The three runtime fast-follow asks all
shipped and are consumed: pack disable/unload landed in v1.4 (§7.1),
subprocess trial isolation (T5, §7.2) and retention pinning for
promoted-from forks (§7.5) both landed in v1.5.

Strict-replay note (CONTRACT v1.3 #4.4b): strict replay diverges on runs
that combine `replay_strict=True` with behaviors subscribed to
`promote.applied`, because marker-derived events are recorded but never
re-derived. This pack IS that subscriber (`mod_promotion` recording), so
its fixtures never assert under strict replay across a promoted block,
and any host enabling strict replay on a store with adoptions must
expect divergence there. Ordinary replay (projection) is unaffected.

## 10. Crash-safety audit and proposals (PROPOSAL — not yet implemented)

A soak-harness bug (the harness re-ran a rotation after losing its
progress file and re-issued a duplicate adoption; see
docs/soak-runbook.md and the v0.7.1 changelog) surfaced a real product
question: the live agent's chassis will crash and restart constantly in
always-on use, so the pack's OWN adoption/disable/boot machinery must be
crash-safe across every window. This section is the audit. **Everything
below §10.1 that is labelled PROPOSAL is investigate-and-propose only —
no semantic change is implemented pending owner decision.** What IS
implemented in this pass is boot's group-by-name dedupe and its pure
DETECTION of a per-pack inconsistency (a loud log plus a `capability_gap`
when a pack has two or more active promotions at boot); boot does not yet
heal or refuse.

### 10.1 Crash-window enumeration

For each step boundary in the real product sequences: the graph state if
the process dies exactly there, and what boot's `reload_adopted_packs`
(post-dedupe) does with it on restart. Verdict OK = state consistent and
boot converges; GAP = state ambiguous/inconsistent or boot does the wrong
thing.

**Two-phase adoption** (`_adopt_executor` then `process_adoption_tickets`
running the canonical order: hash recheck → gates → dry-run → `load_pack`
+ `mod_promotion`(loading) → `promote` → `promote.applied` → recorder
patches to active):

| # | die exactly after… | graph state | boot on restart | verdict |
|---|---|---|---|---|
| 1 | `_adopt_executor` writes the `adoption_ticket` (open), before proposal→`adopting` | ticket open, proposal `pending_approval` | boot loads nothing new; chassis re-processes the open ticket (proposal is still an adoptable status) | OK |
| 2 | proposal→`adopting`, before the chassis runs | ticket open, proposal `adopting` | chassis re-processes the open ticket; converges | OK |
| 3 | hash recheck / gates re-run (writes `gate_result`s) | proposal `gated`/`adopting`, extra `gate_result`s | chassis re-runs; idempotent except DUPLICATE `gate_result` audit rows | OK (audit noise) |
| 4 | `load_pack`, before the `mod_promotion` record | pack loaded in-memory only (lost on crash), no record | load is ephemeral; chassis re-runs and re-loads | OK |
| 5 | `mod_promotion`(loading) recorded, before `promote` | one `loading` record, ticket open | boot SKIPS `loading` (stays down, correct); chassis re-runs the open ticket and creates a SECOND `loading` record — the first is never resolved | **GAP** (orphaned `loading` records accumulate on repeated crash; no double-LOAD, boot ignores `loading`, but the audit trail is wrong) |
| 6 | `promote` applied (`promote.applied` emitted), before `promotion_recorder` flips it to active | promoted STATE is live in the graph; `mod_promotion` still `loading` (recorder only runs on the live `promote.applied`, never re-derived at boot) | boot skips `loading` → the pack is NOT loaded even though its promoted objects are live; chassis re-runs, dry-run promote conflicts, ticket aborts, promotion stuck `loading`, proposal `conflict` | **GAP** (real inconsistency: live promoted state with the pack unloaded and the record stuck `loading`) |
| 7 | `promotion_recorder` patched to active, before ticket→`done` | `mod_promotion` active, ticket open | boot loads the active pack; chassis re-runs the open ticket, dry-run conflicts, aborts the (already-done) work | OK (converges; a redundant abort is logged) |

**Governed disable** (`_process_disable`: `disable_pack` in-memory dereg →
`mod_promotion`→`disabled` → `mod_rollback` → proposal→`disabled` →
ticket→`done`):

| # | die exactly after… | graph state | boot on restart | verdict |
|---|---|---|---|---|
| 8 | `disable_pack` (in-memory), before the `disabled` patch | `mod_promotion` still `active`, ticket open | boot RELOADS the pack as active (the dereg was in-memory and died with the process); chassis then re-processes the open disable ticket and disables it | OK-with-caveat (the "disabled" pack is transiently LIVE from boot until the chassis reprocesses the ticket that same cycle) |
| 9 | `mod_promotion`→`disabled` patch, before ticket→`done` | `mod_promotion` disabled, ticket open | boot leaves it down (correct); chassis re-runs, disable is idempotent, DUPLICATE `mod_rollback` row | OK (audit noise) |

**Watch monitor** (v0.7.0): writes only a `capability_gap`, one-shot with
a dedupe on the promotion id. Die mid-window: at worst the gap is not yet
written; a later `object.created` re-scans and writes it; the dedupe
makes it idempotent. **Verdict OK.**

**Boot itself**: die partway through `reload_adopted_packs` (some packs
loaded, some not). All `load_pack` effects are in-memory and die with the
process; the only persistent write is the hash-mismatch disable+gap, which
is idempotent. Restart re-runs boot from scratch. **Verdict OK (re-runnable).**

### 10.2 The per-pack-name invariant (PROPOSAL)

**The invariant:** at most one ACTIVE `mod_promotion` per pack name.
**What enforces it today:** nothing. `process_adoption_tickets` creates a
new `mod_promotion` unconditionally (adopt.py step 4) and never disables a
prior active promotion of the same pack name. The soak churns UNIQUE names
per rotation (`soak_happy_N`), so it never hit this in normal operation —
the harness bug hit it by re-adopting the same name after amnesia.

**The legitimate flow that stresses it:** the design explicitly allows the
agent to propose UPDATES to its own prior packs as fresh proposals (a
version update adopts the same pack name again). When that happens in the
real product, **nothing disables the prior active promotion** — so a
legitimate version-update adoption ALSO produces two active promotions for
one pack. The soak found this bug via a crash; a version update would find
it via normal use. **This is a real gap.**

Two candidate fixes:

- **(i) Adoption-time supersession.** The canonical adoption order gains a
  step: any existing active promotion for the same pack name is disabled as
  part of the adoption, recorded as `superseded_by` the new promotion
  (audit trail intact, invariant structurally maintained, in one governed
  transaction).
- **(ii) Adoption-time refusal.** Adopting a pack name that already has an
  active promotion is a conflict; the owner must explicitly disable the old
  one first.

**Recommendation: (i) supersession.** The design's stated intent is an
autonomous loop in which the agent updates its own packs; (ii) adds an
owner step to every routine version update, which fights that intent and
would make the common case fail-closed for no security gain (the new
version already went through the full gate/trial/approval chain — the
prior version is not a threat, it is stale). Supersession keeps the loop
autonomous, maintains the invariant structurally (so boot never sees two
actives from the legitimate path), and preserves the audit trail via the
`superseded_by` link. The one nuance to settle at implementation: order
the supersession as part of the SAME canonical adoption transaction, after
the real `promote` succeeds, so a crash mid-adoption can never disable the
old version while failing to install the new one (which would leave the
pack with zero active promotions). **HARD STOP — owner decides before any
code.**

### 10.3 Boot-time posture on ambiguity (PROPOSAL)

Post-dedupe, boot resolves a contradictory promotion set by recency and
logs loudly; for two-or-more actives it also opens a `capability_gap`
(implemented this pass — pure detection, consistent with the existing
hash-mismatch-at-boot precedent). The remaining decision is what boot does
about a genuinely inconsistent state (two actives for one pack, or a GAP
row from §10.1 such as the case-6 `loading`-with-a-live-`promote.applied`):

- **(i) Heal.** Auto-disable the older/orphaned record, load the survivor,
  raise a `capability_gap` documenting exactly what boot did (precedent:
  the hash-mismatch-at-boot path already disables + loud-logs + opens a
  gap). For case 6, "heal" means detecting the live `promote.applied`
  marker for a `loading` promotion's `fork_run_id` and flipping it to
  active + loading the pack.
- **(ii) Fail closed.** Load neither promotion for that pack, park it for
  the owner, raise the gap.

**Recommendation: (i) heal, narrowly.** Boot already has a heal precedent
(hash-mismatch), the agent is meant to run unattended, and fail-closed
would silently drop a pack the owner may depend on until they notice the
park. Heal, but always with the loud log + `capability_gap` so nothing is
silent, and only for the two mechanically-unambiguous cases (pick the most
recent active; resolve a `loading` record whose `promote.applied` is
present in the log). Anything genuinely ambiguous beyond those stays down
and parks. **HARD STOP — owner decides before any heal/refuse code.**

### 10.4 Classification

- **Real product gaps** (not test-harness-only): §10.1 case 5 (orphaned
  `loading` records — minor, audit-only), §10.1 case 6 (live promoted
  state with the pack unloaded — the serious one), §10.2 (no per-pack
  supersession — the one a legitimate version update would hit). The boot
  dedupe + detection shipped this pass narrows the blast radius of all
  three (boot never double-loads and never silently mis-reports), but the
  healing/supersession semantics are the proposals above, deliberately
  unimplemented.
- **Test-harness-only** (fixed this pass, product machinery was correct):
  the durable-state / immediate-persist gap (Gap A) and the missing
  invariant assertion (Gap B) were both in `soak.py`, not in the evolution
  pack. The runtime and the adoption machinery did exactly what they were
  told each time; the harness told them the wrong thing after amnesia.
