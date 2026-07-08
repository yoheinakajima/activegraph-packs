# Evolution Pack Changelog

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
