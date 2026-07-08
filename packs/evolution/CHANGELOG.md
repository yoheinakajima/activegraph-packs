# Evolution Pack Changelog

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
