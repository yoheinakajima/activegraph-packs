# Evolution Pack Changelog

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
