# Evolution Pack Changelog

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
