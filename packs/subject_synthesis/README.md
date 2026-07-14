# Subject Synthesis Pack

Bounded, provider-gated comprehension over what is already confirmed or
owner-scoped (ADR 0043/0045/0046). Synthesis proposes; verdicts promote; the
deterministic derivation stays untouched as the zero-key floor. Synthesis can
never mint a fact, a project, or a memory — every output enters the existing
candidate → owner-verdict → promotion pipelines, and every proposal must cite
the evidence refs it reasons from or it is dropped at commit.

Three surfaces share that doctrine:

**Synthesis** (ADR 0043). A comprehension pass over promoted subject facts
(classed identity/narrative/instruction), the owner's connector taxonomy,
and recurring entities proposes structured identity `profile_candidate`s and
curated `project_candidate`s. Durable `subject_synthesis_request` work unit,
`subject_synthesis_run` receipt (inputs, proposals, deliberate noise,
bounded response sample); prepare/perform/commit mirrors the extraction seam
with a synchronous composition as the pack default.

**Staged connector comprehension** (ADR 0045 §3–4). Connector content
reaches strong reasoning only through hierarchical reduction: eligible
source items (service-owned selection) → batched fast-model leaf summaries
(one structured row per item, evidence refs mandatory — they come from the
payload by construction) → bounded aggregation by group when volume exceeds
the reasoning budget. A connector participates by registering a declared
recipe (selection rule, privacy exclusions, leaf-schema subset, aggregation
grouping, budgets, destinations) — never by growing its own reduction
engine. Leaves may summarize and extract; they may never promote. Packing is
deterministic with recorded coverage — silent truncation is a defect by
definition; model output is sanitized and injection-flagged; every call
records its resolved provider/model and a response breadcrumb; a replayed
commit cannot double-append leaf rows or aggregates.

**The setup draft** (ADR 0046 / D068). The strong cross-source pass reads
only bounded, provenance-bearing inputs (promoted facts, research findings,
comprehension summaries, connector signal maps — deterministic packing,
included refs recorded) and proposes ONE versioned, editable draft whose
items route to their canonical owners across six sections: identity,
narrative, instructions, projects, people, access. No uncited item may
commit. Every item records its prediction before any verdict exists; the
review grammar is accept / reject / edit / merge / reclassify / defer, and
an owner edit supersedes the proposal without counting as a correct system
prediction. Submission promotes through existing pipelines (subject_profile
verdicts, project confirms, access hints) with restartable partial failure;
a new draft version supersedes an unsubmitted head and never mutates a
submitted one. Zero-key stores compose a smaller deterministic draft through
the identical review path.

## Objects

- `subject_synthesis_request` / `subject_synthesis_run` — the synthesis work
  unit and its receipt.
- `comprehension_request` — one staged-reduction campaign over a declared
  recipe.
- `source_item_summary` — one structured, evidence-cited leaf row.
- `comprehension_aggregate` — a bounded middle reduction over one group.
- `setup_draft` / `setup_draft_item` — the versioned draft and its routed,
  predicted, reviewed items.
- `information_access_hint` — an accepted access strategy: which source or
  pattern serves a class of questions. Working knowledge about where
  information lives — never memory, never identity.

## Public API

Synthesis (`packs.subject_synthesis`): `request_subject_synthesis_fn`,
`prepare_subject_synthesis_fn` / `perform_subject_synthesis` /
`commit_subject_synthesis_fn`, `pending_subject_synthesis_requests_fn`,
`run_subject_synthesis_fn`.

Comprehension (`packs.subject_synthesis.comprehension`):
`register_comprehension_recipe` (validated against
`RECIPE_REQUIRED_FIELDS`; leaf schemas subset `LEAF_FIELDS`),
`request_comprehension_fn`, batch and aggregation phases
(`prepare_comprehension_batch_fn` / `perform_comprehension_batch` /
`commit_comprehension_batch_fn`, `prepare_comprehension_aggregation_fn` /
`perform_comprehension_aggregation` /
`commit_comprehension_aggregation_fn`), the pump polls
(`pending_comprehension_batches_fn`,
`pending_comprehension_aggregations_fn`), and
`comprehension_inputs_for_synthesis_fn` for downstream consumers.

Setup draft (`packs.subject_synthesis.draft`): `request_setup_draft_fn`,
`prepare_setup_draft_fn` / `perform_setup_draft` / `commit_setup_draft_fn`,
`compose_deterministic_draft_fn`, `current_setup_draft_fn`,
`review_setup_item_fn`, `merge_setup_project_items_fn`,
`reclassify_setup_item_fn`, `defer_setup_draft_fn`,
`begin_setup_draft_submission_fn`, `resubmit_setup_draft_fn`,
`project_setup_draft_fn`.

## Settings

`SubjectSynthesisSettings`: `subject_ref`, proposal caps
(`max_identity_candidates`, `max_project_candidates`), input budgets
(`max_input_facts`, `max_input_labels`, `max_input_entities`),
`timeout_seconds`, and an optional `model` override. Comprehension model
roles resolve through `packs.llm_provider.resolve_model_for_role`
(fast for leaf batches, reasoning for aggregates/synthesis), and every
run records which model actually served.
