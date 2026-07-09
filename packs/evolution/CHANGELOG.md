# Evolution Pack Changelog

## v0.7.2 — Adoption-time supersession + narrow boot heal (2026-07-09)

Resolves the two crash-safety proposals from the v0.7.1 audit
(docs/evolution-design.md §10), both per the owner's decision. The
per-pack invariant — at most one active promotion per pack name — is now
maintained on two fronts: the adoption path itself, and boot.

### Added
- **Adoption-time supersession (2B, canonical order step 6).** When a
  pack name with an existing ACTIVE promotion is adopted, the prior active
  is disabled and recorded `superseded_by` the new promotion, as part of
  the same adoption transaction (after the real promote, so a crash can
  never disable the old while failing to install the new). This makes the
  invariant structurally maintained by the adoption path and makes the
  designed version-update flow (the agent re-adopting its own prior pack)
  correct for the first time.
- **Narrow boot heal (2C), never guesses, every heal raises a gap.**
  `reload_adopted_packs` now heals only what the event log makes
  unambiguous: a `loading` record whose `promote.applied` marker is in the
  log resolves to active + loads + closes its open ticket (fixes §10.1
  case 6, the serious window — live promoted state with the pack
  permanently unloaded and the chassis wedged); two actives for one pack
  supersede the older by recency and load the survivor; a `loading` record
  with NO marker is parked (fixes §10.1 case 5, so orphaned records cannot
  accumulate). Anything the log does not decide is parked, not resolved.
- Fixtures 34 (supersession: version update + crash-window convergence)
  and 35 (boot heal: heal-on-marker, two-active supersession, park).

### Fixed
- **Duplicate `mod_rollback` on disable-ticket re-run (2A case 9).** A
  disable ticket re-processed after a crash — promotion already disabled,
  rollback already recorded — no longer stacks a second `mod_rollback`.

### Docs
- docs/evolution-design.md: supersession is now a numbered step in the
  canonical adoption order (§1) with the invariant stated; §10 updated
  from proposal to decided-and-implemented, including the boot-heal posture
  and its never-guesses rule, the accepted case-8 transient (one paragraph,
  no code), and the accepted case-3 audit noise.

## v0.7.1 — Soak crash-safety + boot dedupe (2026-07-09)

Fallout from the Replit soak's rotation-15 red flag: a mid-rotation
container kill left the soak harness with a stale progress file, so the
re-run re-issued a duplicate adoption and two packs' behaviors ended up
simultaneously live. The double-adoption was real, but the enabling bug
was in the SOAK HARNESS, not the adoption machinery or the runtime. One
of the three fixes is in product code (boot.py).

### Fixed
- **Boot dedupe (product, `boot.py`).** `reload_adopted_packs` iterated
  every `mod_promotion` in insertion order and overwrote the per-pack
  outcome each pass, so a pack with an active record followed by a
  disabled one was LOADED on the first and reported "disabled" on the
  second — loaded while reported down. Now it groups by pack name,
  resolves each pack's effective state from its full promotion set by
  recency, loads at most once, and reports truthfully. Two or more active
  promotions for one pack is a genuine inconsistency: it loads the most
  recent active only, logs loudly, and opens a `capability_gap` (pure
  detection; boot does not heal or refuse — see the crash-safety proposal
  in docs/evolution-design.md §10).

### Changed (soak harness)
- **Durable state (Gap A).** `scenario_happy` persists its adoption to
  `state.json` the moment it commits, not only at end-of-rotation, and
  the disable-previous step is status-guarded for idempotent re-runs. A
  mid-rotation kill can no longer make the re-run disable a stale target
  and orphan an active promotion.
- **Invariant assertion (Gap B).** The harness now ASSERTS its
  post-rotation invariant (at most one active promotion total) instead of
  only printing the count. A violation is a first-class anomaly: it names
  the offending promotions, flips the digest RED, and persists in the
  anomaly log — the class of bug that let rotation 15 pass all seven
  scenarios silently can never pass silently again.

### Added
- Acceptance fixtures 31 (crash-window reproduction: no orphan after a
  mid-rotation kill + re-run), 32 (the invariant assertion flips RED and
  names both promotions), 33 (boot dedupe: truthful report, load once,
  most-recent-active on two actives).

### Docs
- docs/soak-runbook.md: the harness now self-asserts `active<=1`
  (the observer check is belt-and-braces); mid-rotation kills are safe;
  invariant violations persist in the anomaly log across the daily digest
  overwrite.
- docs/evolution-design.md §10: a crash-safety audit of the product's own
  adoption/disable/boot windows, with two PROPOSAL sections (per-pack
  supersession; boot heal-vs-fail-closed) left unimplemented pending owner
  decision.

## v0.7.0 — Stage-6 watch monitor (2026-07-09)

Post-adoption self-noticing, the last unbuilt piece of the design's
stage 6. An adopted pack that begins failing is now noticed
automatically instead of going unobserved.

### Added
- `watch_monitor`, a fourth behavior. Within `watch_window_events`
  (new setting, default 500) after a pack's promote marker, a
  `behavior.failed` attributable to that pack's own behaviors raises one
  reflection `capability_gap`. Self-noticing, not self-healing: the gap
  flows through the normal loop; nothing is auto-remediated.
- `EvolutionSettings.watch_window_events`.
- Acceptance fixture 30: in-window failure self-notices; a non-adopted
  behavior's failure and a failure past the window do not; a second
  in-window failure does not stack a second open gap.

### Changed
- The `mod_promotion` load-time record now carries its adopted pack's
  behavior names (namespaced `<pack>.<behavior>`, as the runtime emits
  them) in `metadata.behaviors`, the attribution key the monitor uses.

### Notes
- Implementation-forced deviation folded back into the design: the
  runtime suppresses `behavior.*` events from behavior re-matching (loop
  prevention), so `watch_monitor` cannot subscribe to `behavior.failed`
  directly. It reacts to ordinary graph activity (`object.created`) and
  scans the event log instead. `docs/evolution-design.md` §3 stage 6
  documents this.

## v0.6.1 — Platform-aware runaway-memory containment (2026-07-08)

The macOS soak was RED because budget_memory asserted the RLIMIT_AS
memory net fires, which is only true on Linux. On macOS the memory net
is deliberately OFF (Darwin cannot set address-space limits; 1.7.1
degrades it loudly), so a fixed 600MB allocation completed and the
trial passed, breaking `assert verdict == "fail"` every rotation.

### Changed
- budget_memory now protects CONTAINMENT (true on both platforms), not
  "the memory net fires" (true only on Linux). It keys off the runtime's
  own memory-net signal (`SoakHarness._memory_net_available`, from
  `activegraph.sandbox.preflight` warnings), never `sys.platform`. Memory
  net live (Linux): fixed over-cap allocation, contained by the memory
  net (`materialization_failed`), UNCHANGED so the in-progress Linux run
  is unaffected. Memory net OFF (macOS): an unbounded runaway contained
  by the wall-clock kill (`limits_exceeded`).
- `_latest_child_failure_detail` takes `exclude_ids`; run_rotation
  snapshots the failure-carrying objects before each scenario so an
  anomaly reads only THAT scenario's child detail. Fixes the
  attribution bleed where budget_wallclock's error rendered under
  budget_memory.

### Added
- Fixture 29: the Linux branch (real, memory-net containment), the macOS
  branch (forced net-off, wall-clock containment), and the attribution
  fix. Twenty-nine scenarios total.
- Runbook: budget_memory documented as platform-conditional; a
  memory-net-OFF warning on macOS is expected; the real red flag is a
  runaway that `completed` (escaped all nets).


## v0.6.0 — The LLM author, MOCK model only (2026-07-08)

Runtime floor >=1.7.1 (macOS RLIMIT_AS fix, memory-net-degrades-loudly,
activegraph.sandbox.preflight and source-populated TrialReport.detail).
The LLM author is BUILT and proven against a MOCK model, keyless. Live-
model operation stays gated on the soak finishing green.

### Added
- author.py: the LLM author (docs/llm-author-design.md). `assemble_frame`
  builds the four-section origin-classified context (charter, structured
  gap fields, target surface, verified-owner text) as pure data;
  excluded origins (memory, agent_profile, capability_result.output_data,
  web/MCP text, unverified senders, prior rationales, the exception
  MESSAGE) never enter. `draft_proposal` is the one-shot pipeline: seal
  the drafting_context, call the injected model with pure data and no
  tools, take four source bodies, stamp the agent_ name and provenance
  in pack code (the model touches neither), and submit. Rate caps: one
  draft in flight per gap, daily cap (new EvolutionSettings.
  max_drafts_per_day), no redraft-from-rejection.
- author_charter.md: the author's system prompt, hash-pinned, human-PR-
  only, never authorable.
- author_frame: charter_text_and_hash; recompute_drafting_taint also
  content-scans admitted owner inputs (the §3d tripwire).
- Mock author model (fixtures/candidates.py::mock_author_model) and
  fixtures 25-28: origin assembly with every excluded origin provably
  absent; the pipeline and the four folds under the real author; a
  tainted context suspending even when mock output is pristine plus the
  three rate caps; and the gate-3 end-to-end render of a MOCK-LLM
  proposal. Twenty-eight scenarios total.
- drafting_context gains a `day` field for the daily rate cap.

### Status (docs/llm-author-design.md)
- Author BUILT; gates 1, 2, 3, 6 MET via mock; gate 4 fixtures run
  against the real author path; gate 5 (green soak) is the remaining
  blocker. Live-model operation on a credentialed machine is gated on
  the soak's green finish.


## v0.5.2 — activegraph 1.7.0: soak green on the fixed trial child (2026-07-08)

The whitelist question is answered in the runtime: 1.7.0 computes the
trial child's import path from the parent's resolved sys.path, so the
child imports activegraph on any install the parent can (editable,
venv, Nix/Replit) with the allow-list still closed. Scope correction
absorbed: the child-import break was NOT Replit-specific; a stock macOS
venv failed identically on 1.6.0. The prior "soak runs green" was on
1.6.0 (the broken version) on an editable install that masked the bug;
this is the first honest soak on the fixed path.

### Changed
- Runtime floor raised to activegraph >=1.7,<2.0 (pyproject + all 20
  manifests): this cycle depends on the automatic child import-path
  computation and activegraph.sandbox.preflight.
- The soak preflight now delegates to the runtime's canonical
  activegraph.sandbox.preflight (the probe that stays correct as the
  sandbox evolves); SoakHarness.preflight is a thin wrapper turning its
  SandboxStartupError into the soak's REFUSING TO RUN message. The
  rolled-own null-trial probe is gone. TrialReport.detail (now populated
  at the source in 1.7.0) flows into the digest through the same graph
  records trial.run_trial writes.
- Runbook environment-constraint section updated: 1.7.0 resolves the
  non-CI-install break; the preflight delegates to the runtime probe.
- Fixture 24 patches activegraph.sandbox.preflight (not run_forked_trial)
  to exercise the refusal wrapper.

### Verified
- One clean rotation on 1.7.0 from a fresh store, all seven paths OK,
  digest GREEN (the green light for the real soak clock).


## v0.5.1 — Soak preflight and never-opaque crash detail (2026-07-08)

Two defects surfaced by a Replit soak RED (root cause environmental:
the runtime's trial-child env whitelist strips REPLIT_PYTHONPATH, so
the child cannot import activegraph on Replit). The whitelist itself is
a runtime security boundary and is NOT touched here; the fix is
soak-side surfacing plus a preflight, with the whitelist question
couriered to the runtime.

### Added
- Soak preflight (soak.py, Defect 2): before rotation 1, one real
  minimal `run_forked_trial` probes that a trial child can start on this
  box. If it cannot (the child cannot import activegraph under the
  sandbox env whitelist), the soak refuses to run with a clear message
  and exit 2, instead of accumulating identical silent crashes.
  `--skip-preflight` opts out. Runbook documents the environment
  constraint.
- Fixture 24: the preflight passes on a capable box and refuses (naming
  the real cause) on a patched incapable one; a trial-child failure is
  surfaced in the digest and anomaly log, never opaque.

### Changed
- Trial-child failures are never opaque in the digest (Defect 1): the
  child's outcome and detail (TrialReport.detail, the stderr tail the
  runtime surfaces) reach the digest per-path line and the anomaly log,
  alongside the soak-side traceback rather than replacing it.
  `trial.run_trial` now returns `detail` on the fixture-gate-fail path
  and records `child_detail` on mod_trial.eval_summary.


## v0.5.0 — Author-frame enforced boundaries (2026-07-08)

The LLM-author design passed review (build gate 2). The four required
changes turn asserted trust boundaries into enforced ones, built ahead
of the author so its build is a wiring step. The author stays unbuilt
(remaining gates: a green soak, and the mock-model assembly fixtures).

### Added
- author_frame.py: `validate_structured_fields` (charset-checks admitted
  structured fields against the manifest identifier pattern; a
  prose-shaped capability name or an off-allow-list field path is
  rejected) and `recompute_drafting_taint` (unions injection flags over
  a drafting record's ADMITTED object ids, never its stored flags
  field). AUTHOR_CHARTER_FILENAME, the reserved charter path.
- Charter integrity gate (`static:reserved_paths`, analysis.
  check_reserved_paths, EvolutionSettings.reserved_paths): runs FIRST,
  refusing any proposal that targets the charter or another human-PR-only
  path. §8's "by hand only" is now a gate.
- Fixtures 21 (charter reserved-path refused first), 22 (taint
  recomputed from admitted ids; a lying record cannot launder), 23
  (prose structured field and off-allow-list field path both refused).
  Twenty-two scenarios total.

### Changed
- submit_proposal_fn, with a drafting_context_id, recomputes the taint
  union from admitted ids and refuses submission (ValueError) when a
  structured field fails the charset check. The stored injection_flags
  field is no longer trusted for enforcement.


## v0.4.1 — Retention concurrency verdict folded (2026-07-08)

The runtime ruled the retention offline requirement per-RUN (CONTRACT
v1.5 #2 addendum 2b): retiring a fork run is sanctioned while a live
runtime is attached to OTHER runs in the same store, pinned by the
runtime's test_retire_fork_per_run_while_parent_runtime_is_live.

### Changed
- `boot.retire_unpinned_trial_forks` docs now state the per-run
  concurrency ruling and the two conditions the pack keeps: never race
  a pin-creating op against retirement of the same run (satisfied by
  retiring only terminal-status forks; a lost race degrades an audit
  walk but destroys nothing, archived rows stay readable via
  iter_archived), and never compact/retire a run under a live runtime
  attached to that same run (the snapshot-event id collision, the one
  real hazard). No code change: fixture 18 was already correct, and the
  demo server's pre-boot placement already honors the strict caveat.
- Fixture 18 documents and asserts the sanctioned pattern explicitly:
  the parent runtime stays live on the store throughout while
  pins()/retire() and the housekeeping helper operate on the fork runs.
- Design §7.2 notes v1.7 `extra_packs` exists for cross-pack trials
  (candidate-only isolation stays canonical; no floor bump), and §7.5
  records the concurrency ruling.

## v0.4.0 — Soak harness + drafting records (2026-07-08)

Gate 5's clock starts; gate 3 becomes a wiring step.

### Added
- The soak harness (soak.py + docs/soak-runbook.md): the full loop
  unattended on a keyless machine. Each rotation boots a fresh runtime
  against the persistent store (boot housekeeping, principal re-index,
  adopted-pack reload every time) and walks seven paths with the
  scripted author: happy end to end with a watch window and governed
  disposal of the previous rotation's pack, conflict-park at
  needs_owner, disable-restart staying down, one candidate per runtime
  budget net (event flood, wall-clock hang, memory hog), and a tainted
  proposal sitting suspended. Anomalies are recorded with tracebacks,
  never swallowed; the daily digest (markdown, zero setup) carries
  status, cumulative path counts, graph counts, and the stop-condition
  readout. Fixture 19 runs one full rotation in CI.
- Drafting records (llm-author-design §4, ahead of the author): the
  drafting_context object type, submit_proposal_fn inheriting the
  record's injection_flags deterministically, and the review page
  rendering "What the author read" (origin sections, charter hash,
  taint union) beside the diff. Fixture 20: a tainted record suspends
  with a loud banner and no approve button; a referenced-but-absent
  record renders as a refusal.
- Scripted-author candidates now parameterize their object-type name
  (log_type), because the runtime enforces type ownership and soak
  rotations adopt many packs side by side.


## v0.3.0 — Subprocess trials + retention pins (2026-07-08)

Consumes activegraph v1.5.0; both remaining runtime blockers close.
Gate 1 of the LLM-author gate list flips to shipped-and-consumed.

### Changed
- Stage 3 rewritten onto the runtime's sandbox
  (activegraph.sandbox.run_forked_trial): ALL candidate execution now
  happens in a fresh-interpreter child (fixture gate, in-sample replay,
  held-out replay), pin-verified before any import, under the runtime's
  three nets (rlimits, wall-clock kill, event budget). The parent never
  imports candidate code at trial time. Budgets map onto the runtime
  nets; the pack double-enforces nothing.
- The chassis trial driver (trial_driver.py) joins the authored file
  set as fixtures/trial_scenario.py: the sandbox requires the scenario
  inside the bundle-hashed root, so the driver is included verbatim by
  authors, verified byte for byte by the new static:trial_driver gate,
  and the held-out split freezes at proposal creation under the same
  pin the owner approves. The residue sweep (design 7.3) now runs
  inside the child, driver-owned.
- Authored fixture contract: fixtures/run_fixtures.py must expose
  `def main(rt)` (the trial-child entrypoint); checked statically in
  the file_set gate.
- Trial forks persist in the store: the in-process fork registry is
  deleted, adoption reloads the fork by run id, and a restart between
  trial and adoption no longer forces a re-trial.

### Added
- Retention housekeeping (boot.retire_unpinned_trial_forks): disposable
  trial forks archive at boot through the runtime retention API;
  promoted-from forks refuse with RetentionPinnedError (the pin set
  dominates unconditionally); in-flight forks are kept by proposal
  status. Demo server runs it before the runtime attaches.
- Settings: trial_wall_clock_seconds, trial_max_rss_bytes.
- Fixtures 17 (subprocess isolation: a runaway import dies in the
  child, the parent stays alive) and 18 (retention pins). Seventeen
  scenarios total.



## v0.2.0 — Decision surface, residue policy, retry cap (2026-07-08)

Closes three scare-list items before any product wires an author in.

### Added
- The decision surface (review.py): build_review assembles one
  proposal's full review model from graph state alone; render_review_html
  renders the one page an owner reads before approving (author banner
  first, injection flags loud, gap, declared surface including consumes,
  every gate verdict, trial numbers with the fork run id, and the FULL
  per-file source diff against the currently adopted version). Served by
  the demo server at /approvals/review; /approvals content-negotiates
  (JSON for API clients, the review index for browsers). Fixture 14.
- Bounded conflict retries (chassis.py): sweep_evolution wraps ticket
  processing; a conflicted adoption is re-gated, re-trialed at
  parent-now, and requeued under the same approved call at most
  max_conflict_retries times (new setting, default 2), then parked at
  the new TERMINAL proposal status needs_owner. Ticket processing
  refuses parked proposals; the /approvals index lists them. The demo
  server tick driver now calls the chassis sweep. Fixture 16.

### Changed
- Trial replay residue policy (design §7.3, RESOLVED): a passing fork
  removes every object and relation created after the candidate loaded
  (replayed input copies and everything derived from them) before
  adoption sees it, so promote carries no replay scaffolding into the
  parent. Patches to pre-existing shared state still promote. The sweep
  counts land in mod_trial.eval_summary.replay_residue_removed, and the
  trial's diff summary now reflects the post-sweep delta. Fixture 15.

## v0.1.0 — Initial release (2026-07-08)

The reason the last two months of groundwork exist: agent-authored packs
under governance, per docs/evolution-design.md, on activegraph >=1.4
(promote with apply-time delta validation, disable_pack, the manifest
reference implementation).

### Added
- Object types: capability_gap, mod_proposal, gate_result, mod_trial,
  mod_promotion (recorded at LOAD time, status loading -> active),
  mod_rollback, adoption_ticket. Five relation types.
- Behaviors: gap_detector (repeated capability failures, deterministic
  taint inheritance), proposal_gatekeeper (taint check, then the static
  gates), promotion_recorder (the single promote.applied reaction point;
  quiescent apply means nothing else fires).
- Static gates (analysis.py + gates.py): file set, manifest validity
  (runtime validator), content + BUNDLE hash pins, two-way
  declared-vs-actual, import allow-list (fixtures exempted onto their
  own list), banned constructs, reserved namespaces +
  NEVER_LLM_CALLABLE names, size caps, injection scan (suspends, never
  rejects).
- Fork trials (trial.py): candidate fixtures in a key-stripped
  subprocess with timeout, fork at parent tip, in-sample replay, then
  held-out replay touched exactly once, event budget, candidate-only
  failure attribution from trace.failures(), rt.diff summary.
- Two-phase adoption (adopt.py): governed capabilities
  evolution.adopt_proposal (critical) and evolution.disable_promotion
  (high) whose REGISTRATION refuses auto-approvable-critical policies
  and unverified identity; phase two (process_adoption_tickets) runs
  the canonical order between frames: bundle pin -> gates re-run ->
  dry run -> load_pack + mod_promotion(loading) -> promote. Disable is
  immediate deregistration (rt.disable_pack) plus boot exclusion.
- Boot persistence (boot.py): adopted packs re-materialize from graph
  artifacts at startup, bundle-hash checked; mismatch disables loudly
  and opens a capability_gap.
- Twelve deterministic acceptance fixtures with a scripted author
  (fixtures/candidates.py); no LLM, no keys, no network.
- Demo server wiring behind ACTIVEGRAPH_EVOLUTION=1.
