# Changelog

## 0.4.0 — review horizons are accepted explicitly (2026-07-16)

ADR 0051 / D076 — the understanding-to-review closure round. The first
fully-keyed owner run submitted a one-item setup while a 48-item cumulative
update sat open; the update then targeted a submitted draft and became
permanently unreviewable. This release closes the semantic gap:

- Delta dispositions are a closed vocabulary (`open` / `applied` /
  `dismissed` / `deferred` / `superseded`, now schema-complete); `open`
  blocks acceptance, `applied` stages UNDECIDED rows (staging is never
  acceptance), `deferred` stays visible and is never counted reviewed.
- `begin_setup_draft_submission_fn` is an explicit horizon acceptance: it
  atomically re-checks open deltas (owner-language conflict + Review route
  on refusal — a stale client cannot submit around event ordering) and
  undecided items (refused unless the caller explicitly acknowledges with
  `defer_undecided=True`), then records `accepted_horizon`, `accepted_by`,
  and every known delta's disposition on the draft.
- Post-acceptance material lands as ONE cumulative delta against the
  accepted head (never a spontaneous new draft version); the head stays
  immutable — its late input horizons stamp the delta's coverage, and
  `consumed_input_fingerprint_fn` reads the newest delta's fingerprint.
- `apply_understanding_delta_fn` on an accepted head promotes the delta
  into a SUCCESSOR review batch (`setup_draft` source `successor`,
  `review_context: workspace`, frozen immediately): only new/changed rows,
  every one undecided, unchanged keys never re-ask, owner edits win,
  predecessor verdicts/receipts untouched, replay-idempotent (the same
  delta always answers with the same successor). Legacy stores holding a
  submitted draft plus an older open delta recover through this path with
  zero history rewrite.
- Project proposals require qualifying non-topology evidence: an
  `integration_profile` (labels/surfaces) corroborates but never proposes
  (drops counted as `dropped_topology_only`); synthesis project rows also
  require a description and rationale (`dropped_low_quality`); the
  deterministic floor skips topology-only candidates; legacy delta rows
  that fail the gate stay visible but flagged "supported only by tool
  topology; needs corroboration" (needs-your-call, never bulk-approved).
  The draft prompt now distinguishes named projects/products/funds from
  generic business functions, companies, topics, and access strategies.
- `project_setup_draft_fn` exposes `submission_blockers` (open updates,
  their item count, undecided items) plus the draft's `review_context`,
  `successor_of`, and `accepted_horizon`; new `open_understanding_deltas_fn`
  lists the unresolved owner work.
- Conformance: `tests/test_review_horizon.py` (14 tests) pins the ADR 0051
  §12.1/§12.2 matrix, including the preserved owner store's exact legacy
  shape.


## 0.3.0 — the governed agentic loop (2026-07-14)

- Synthesis convergence + one-update review (2026-07-14 night, onboarding
  product closure): the live keyed run recomposed one draft 28 times
  because synthesis-authored candidates re-versioned the working
  understanding, which re-scheduled synthesis. Cuts, each test-pinned in
  `tests/test_synthesis_convergence.py`:
  - working entries carry `origin` (source/owner/synthesis);
    synthesis-origin entries never schedule reinterpretation and reduce to
    stable identity in the content hash (naming/score variance cannot move
    it);
  - candidate refresh patches are conditional (no no-op "refreshed the
    proposal" events);
  - `synthesis_input_fingerprint_fn` digests every EXTERNAL input; drafts
    stamp the fingerprint they consumed (`coverage.input_fingerprint`,
    frozen heads too) and `request_setup_draft_fn` refuses a re-request
    over an unchanged horizon — one pending synthesis per input horizon;
  - ONE cumulative understanding delta per frozen snapshot: a new delta
    supersedes unresolved predecessors, carries every unresolved predecessor
    item forward by semantic key (newest presentation wins), and inherits
    `deferred` when it carries nothing the owner hasn't seen; this prevents a
    later smaller source pass from making earlier research/projects disappear;
  - applying a delta supersedes same-key predecessors (owner verdicts and
    comments travel onto refreshed content; owner edits always win) —
    never a duplicate active item; the setup-draft projection now hides
    superseded rows;
  - deterministic `possible_overlap_clusters_fn` +
    `dismiss_overlap_cluster_fn` (shared-token/name-prefix clusters;
    flagging, never auto-merge); `review_setup_items_fn` batch verdict;
  - budget pauses mint an answerable wrap-up/extend owner question; the
    deterministic floor's synthesize/stop are budget-exempt (a campaign
    can always end); review resolution settles any non-terminal campaign
    `completed/review_ready`;
  - host-MEASURED provider usage (`usage` on proposal/draft outcomes)
    charges campaign budgets via `charge_campaign_usage_fn`;
  - access proposals must be actionable strategies (question class +
    strategy + reason; label inventories are dropped and counted as
    `dropped_low_quality`).
- Live-run hardening (2026-07-14 evening): a pausing `ask_owner` move now
  mints its owner question in the same commit (`record_coordinator_move_fn`),
  so a paused campaign always presents something answerable; the validator
  rejects `ask_owner` without question text (`ask_owner_needs_question`)
  instead of stranding the campaign; the proposal packet carries
  `verdict_reasons` on prior moves plus `available_move_kinds` /
  `unavailable_move_kinds`, and the prompt forbids re-proposing rejected
  moves unchanged — a live keyed run burned its whole window re-proposing
  one rejected `inspect_source` and then deadlocked onboarding on a
  questionless pause.
- Understanding affordances (ADR 0047 §2): the typed registry by which any
  source joins a comprehension campaign — teaches, capabilities/scopes,
  schemas, privacy/outward-disclosure, reductions, drill-down permission
  with bounds and a source-owned selector, budgets, destinations, and
  coverage requirements. Validation runs at registration.
- Source lenses and one versioned working understanding (ADR 0047 §3–4):
  every contribution separates `support_refs` from `context_refs`; borrowed
  context never corroborates; entries carry authority classes
  (owner_confirmed / hypothesis / unresolved / denied); material changes
  schedule targeted, version-pinned reinterpretation — never a global rerun.
- The dynamic coordinator (ADR 0047 §1, §5): typed move grammar
  (inspect_source / outward_query / reduce_fast / drill_down /
  align_entities / ask_owner / propose_amendment / synthesize / stop), a
  deterministic host validator over consent, plan versions, scope, action
  class, privacy authority, provider support, budgets, and owner-decision
  boundaries; a zero-key deterministic proposer preserving today's
  lifecycle; the reasoning-role model proposer on the prepare/perform/commit
  seam; owner questions that pause and resume the campaign; and bounded,
  fully-recorded evidence drill-downs whose uncited findings are dropped.
- Three logical model roles (ADR 0047 §6) recorded on every call:
  reasoning/coordinator, balanced (aggregation moved here from fast), fast.
- Stable review snapshots (ADR 0048 §3): the first owner decision — a
  verdict, a comment, or an explicit `begin_setup_review_fn` — freezes the
  head draft. From then on new synthesis (keyed or deterministic) lands as
  an `understanding_delta` diffed by stable semantic keys
  (`semantic_item_key`): unchanged items silently keep their verdicts, new
  keys arrive as `new`, same-key presentation changes as `changed`, and
  changes against owner-accepted items as `conflicting` with predecessor
  refs. Deltas apply (rows join review as FRESH proposals with candidate
  reuse), dismiss, or defer — durable either way. An unreviewed head still
  supersedes exactly as before.
- The typed correction grammar (ADR 0048 §4): rejections carry a typed
  `correction` (not_me / duplicate / incorrect / not_useful / wrong_type /
  wrong_grouping); `comment_setup_item_fn` records durable owner comments
  that freeze review and never count as correct predictions;
  `split_setup_project_item_fn` splits one project item into fresh
  candidate-backed proposals.
- The review cohort (ADR 0048 §3): `freeze_review_cohort_fn` ("review what
  I have now") closes the selected-source cohort at the current horizon;
  the deterministic proposer then synthesizes without waiting and late
  results arrive as deltas. `review_cohort_state_fn` projects it.

## 0.2.0 — staged comprehension and the setup draft (2026-07-13)

- Connector comprehension recipes (ADR 0045 §3–4): a connector participates
  in comprehension by registering a declared recipe (selection rule,
  privacy exclusions, leaf-schema subset, aggregation grouping, budgets,
  destinations) — never by growing its own reduction engine. The neutral
  leaf row schema is fixed here.
- Staged reduction: eligible items → batched fast-model leaf rows (one
  structured row per item; evidence refs come from the payload by
  construction) → bounded aggregation by group when volume exceeds the
  reasoning budget. Coverage is recorded at every stage — silent truncation
  is a defect by definition; model output is sanitized and
  injection-flagged; every call records its resolved provider/model and a
  response breadcrumb. A commit replayed after a crash cannot double-append
  leaf rows or aggregates. Leaves may summarize and extract; they may never
  promote.
- The setup draft (ADR 0046 / D068): one versioned, editable proposal of
  the owner's world from bounded provenance-bearing inputs (promoted facts,
  research findings, comprehension summaries, signal maps — deterministic
  packing, included refs recorded), with six routed sections (identity,
  narrative, instructions, projects, people, access). No uncited item may
  commit; every item records its prediction before any verdict exists; the
  review grammar is accept/reject/edit/merge/reclassify/defer, and an owner
  edit supersedes without minting a prediction win. Staged submission
  promotes through the canonical pipelines (subject_profile verdicts,
  project confirms, access hints) with restartable partial failure; the
  zero-key deterministic composer reaches the identical review gate.
- New `information_access_hint` object: an accepted access strategy is
  working knowledge about where information lives — never memory, never
  identity.

## 0.1.0 — determinism floors, synthesis proposes, verdicts promote (2026-07-13)

- New pack (ADR 0043 / D064): a bounded, provider-gated comprehension pass
  over promoted subject facts (classed identity/narrative/instruction),
  the owner's connector taxonomy, and recurring entities.
- Proposes structured identity `profile_candidate`s anchored to
  owner-scoped evidence and curated `project_candidate`s (kind
  `synthesized`) — every proposal cites refs from the prepared input or is
  dropped at commit; verdicts remain the only promotion.
- Durable `subject_synthesis_request` work unit + `subject_synthesis_run`
  receipt (inputs, proposals, deliberate noise, bounded response sample).
- Three-phase seam mirrors semantic_extraction's deferred shape (ADR
  0041): prepare/perform/commit for host pumps, with a synchronous
  composition as the pack default.
