# Evolution Pack: design

**Status: DESIGN. Implementation is blocked by the pack manifest spec
(docs/manifest-spec.md, task #4) and the activegraph v1.3.0 pin bump
(task #10, which delivers `promote`). Nothing in this document is code
yet. The goal of this document is to make the implementation boring.**

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

Consumption pattern for adoption, in order, inside one governed
executor. This is the single canonical order; §3 stage 5 restates it
with rationale:

1. Recompute gates against the proposal's pinned content hash.
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

## 2. Graph vocabulary

New object types (all with full schemas at implementation time):

| Type | Purpose | Key fields |
|---|---|---|
| `capability_gap` | Something the assistant could not do | `kind` (tool_failure, unhandled_intent, reflection, owner_request), `description`, `evidence_refs`, `status` |
| `mod_proposal` | One candidate pack version | `gap_id`, `pack_name`, `pack_version`, `manifest` (per manifest-spec), `source_artifact_ids`, `content_hash`, `rationale`, `authored_by`, `status` |
| `gate_result` | One gate's verdict on one proposal | `proposal_id`, `gate` (static, fixtures, in_sample, held_out), `verdict`, `details` |
| `mod_trial` | One fork trial of one proposal | `proposal_id`, `fork_run_id`, `forked_at_event`, `eval_summary`, `diff_summary`, `failures`, `verdict` |
| `mod_promotion` | A completed adoption | `proposal_id`, `trial_id`, `promote_marker_event_id`, `from_run`, `applied_counts`, `status` (active, disabled) |
| `mod_rollback` | A disable/rollback action | `promotion_id`, `method`, `reason`, `at` |

Proposal source files are stored as Core `artifact` objects, one per
file, hashed individually; `mod_proposal.content_hash` is the manifest's
content hash over the same file set MINUS `manifest.toml`, computed
exactly per manifest-spec §4 (the canonicalization there is normative
for all three places this hash gets recomputed). The hash is the pin:
what the owner approved is byte-identical to what the executor loads,
or the executor refuses.

Proposal status machine:

```
drafted -> gated -> trialed -> pending_approval -> promoted
                \-> rejected (gate fail)      \-> denied
trialed -> conflict (promote aborted) -> re-trial or abandoned
promoted -> disabled (rollback)
any -> suspended (injection taint, see §6)
```

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

An `@llm_behavior` authors a candidate: a manifest (per manifest-spec)
plus source files. Constraints on what may be authored:

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
`gate_result`:

1. **Manifest validity**: parses, schema-valid per manifest-spec,
   version sane, declared runtime range includes the pinned runtime.
2. **Hash integrity**: manifest content hash matches the stored
   artifacts.
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

### Stage 3: fork trial

```python
fork = parent_rt.fork(at_event=parent_tip)
fork.load_pack(candidate, settings=proposal_settings)
run_scenarios(fork)          # see below
failures = fork.trace.failures()
diff = parent_rt.diff(fork)
```

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

### Stage 5: adoption (the governed executor)

The `evolution.adopt_proposal` executor, running only after approval,
in the §1 canonical order:

1. Recompute the content hash of the stored artifacts and compare to
   the approved proposal's hash. Mismatch aborts loudly. This closes
   the approve-then-swap window: what loads is what was reviewed.
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

### Stage 6: monitor, disable, roll back

- **Watch window**: after adoption, a behavior watches
  `behavior.failed` events from the candidate's behaviors for a
  configurable window and raises a `capability_gap` of kind
  `reflection` if the failure rate is nonzero. Self-noticing, not
  self-healing: fixes are new proposals through the same loop.
- **Disable (v1 rollback)**: `mod_promotion.status = disabled` via a
  governed capability. The chassis is the enforcement point: adopted
  packs load at boot from the graph's active `mod_promotion` records,
  so a disabled pack stays down after restart. Live unload does not
  exist in the runtime today; until it does, disable means "no new
  loads plus restart to evict", stated plainly.
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
    trial_max_events: int = 2_000
    trial_max_llm_calls: int = 20
    heldout_segment_events: int = 200
    watch_window_events: int = 500
    import_allow_list: list[str] = [...]   # the §3 stage-2 list
    reserved_namespaces: list[str] = [...]
```

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

**T4: approve-then-swap.** Source mutates between owner approval and
executor load. Containment: content hash pinned on the proposal,
recomputed at adoption and at every boot re-materialization.

**T5: the trial itself as attack surface.** Candidate code runs inside
the fork during stage 3, before any approval. This is the sharpest
honest edge in the design: the fork isolates *graph state*, never
process state. A malicious candidate could attempt process-level harm
from inside the trial. Mitigation: static gates run BEFORE any load
(import allow-list plus banned constructs is exactly the anti-process
filter), trials run with budgets, and the roadmap asks the runtime for
subprocess trial isolation as a fast-follow. Until then, stage 2 is the
only wall before stage 3, and the design treats any stage-2 bypass as a
critical bug, tested adversarially in the acceptance fixtures.

**T6: gate erosion.** Future contributors relax a gate default or
auto-approve `critical`. Containment: registration-time refusal (§3
stage 4), fixtures that assert the refusal, and `enabled: False` as the
shipped default.

## 7. Explicit open problems

1. **No unload.** The runtime cannot evict a loaded pack. Disable is
   boot-time enforcement plus restart. Runtime ask filed as a
   fast-follow (`disable_pack` or behavior deregistration).
2. **Trial process isolation.** Fork isolates state, not the process
   (T5). Runtime ask: optional subprocess execution for fork trials.
3. **Replay conventions.** Per-channel input re-injection needs a
   spec; v1 limits itself to chat segments and pack fixtures.
4. **Behavioral regression depth.** v1's comparator is failures plus
   fixture assertions. Graph-state diffing against expected shapes is
   future work.
5. **Fork log retention.** Two-hop provenance (promoted entity ->
   marker -> fork log) requires the fork's log to outlive the trial.
   The runtime's future compaction design must treat promoted-from
   fork runs as pinned.
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
6. **Injection taint**: a gap whose evidence carries injection flags
   yields a `suspended` proposal that no gate will touch until cleared.
7. **Self-approval attempt**: a generated pack declaring
   `approve_capability` fails the reserved-namespace gate; forcing it
   into the registry anyway is refused by `as_llm_tool`.
8. **Hash pin**: artifact bytes mutate after approval; the adoption
   executor aborts; nothing loads.
9. **Restart persistence**: adopted pack reloads at boot from
   `mod_promotion`; a disabled one stays down; a hash-mismatched one is
   disabled loudly.
10. **Registration refusals**: gateway settings that would auto-approve
    `critical` make `evolution.adopt_proposal` registration fail, and
    so does a runtime without identity_auth loaded or without a
    registered approver-role principal (unverified-mode adoption must
    not exist).
11. **Taint inheritance**: a reflection review that consumed a flagged
    capability_result produces a gap carrying the inherited flags even
    when the (scripted) reviewer omits the flagged source from
    evidence_refs; the resulting proposal is `suspended`.
12. **Loading-state tracking**: a real-promote conflict after
    `load_pack` leaves `mod_promotion` at `loading`; disable works on
    it, and a restart does not re-load it.

## 9. Dependencies and sequencing

Blocked by: manifest spec (#4) locked and consumed; runtime v1.3.0 with
promote and the DX items (#10). Consumes: task #6 posture
(`injection_flag`, `NEVER_LLM_CALLABLE`), tool_gateway approval
machinery, identity_auth approver verification, artifacts (core),
schedule (reflection ticks). Runtime fast-follow asks, in priority
order: subprocess trial isolation (T5), pack disable/unload (§7.1),
compaction pinning for promoted-from forks (§7.5).

Strict-replay note (CONTRACT v1.3 #4.4b): strict replay diverges on runs
that combine `replay_strict=True` with behaviors subscribed to
`promote.applied`, because marker-derived events are recorded but never
re-derived. This pack IS that subscriber (`mod_promotion` recording), so
its fixtures never assert under strict replay across a promoted block,
and any host enabling strict replay on a store with adoptions must
expect divergence there. Ordinary replay (projection) is unaffected.
