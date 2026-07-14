# Web Research Pack

Owner-consented web research (ADR 0040 / D060 / ADR 0045): never ambient,
always plan-bound. The seed queries are the ingestion plan's surfaces — each
one individually strikeable before approval — and they derive ONLY from
owner-confirmed subject facts (promoted name/company facts plus the alias
set) and caller-attested confirmed terms. Email addresses never enter an
outward query: an address in a query is a disclosure the owner did not make.

An approved plan runs as a bounded adaptive campaign. Every query is recorded
before it executes: seeds as plan surfaces, follow-ups as frontier rows with
lineage (parent query, motivating findings, confirmed scope, round). A
follow-up that names a new entity or a sensitive topic pauses as a reviewable
`research_scope_amendment` — research never silently broadens outward.
Deterministic budgets (rounds, total queries, pages, a novelty floor) are
authoritative over any model recommendation; each round's discovered URLs
ingest exclusively through the governed public-presence gateway (budgeted,
recorded, injection-scanned); findings join the existing "is this you?"
verdict path; and the stop reason is part of the run receipt.

Search executes through the provider-neutral adapter: one neutral
request/result contract with explicit per-provider mappings (Anthropic's
`web_search_20250305` tool block, OpenAI's `web_search` tool). Provider
dialect lives in the adapter and nowhere else; unsupported providers fail
closed; zero-key resolution reports unavailable instead of pretending; model
output is untrusted by default — parsed tolerantly, injection-scanned,
bounded.

The pack is a full connector-control citizen: deferred three-phase execution
beside the synchronous path (settlement byte-identical), run observation,
learning delta with planned-vs-actual, a documents-family native view per
run, and commit-recovery idempotency (a replayed commit cannot double-append
findings).

## Objects

- `web_research_run` — one campaign bound to its approved plan: findings
  ledger, rounds executed, calls, stop reason.
- `research_query` — one recorded frontier entry with origin (seed or
  follow-up), lineage, scope entity, round, and execution status.
- `research_scope_amendment` — a scope-expanding follow-up paused for owner
  review; approve or decline is an explicit verdict.

## Public API

Plan lifecycle (`packs.web_research.plan`): `propose_web_research_plan_fn`,
`derive_research_queries`, `derive_scope_terms`,
`execute_web_research_plan_fn`, and the deferred phases
`prepare_web_research_execution` / `perform_prepared_web_research` /
`commit_web_research_execution`; `register_web_research` wires the executors
into connector_control.

Campaign internals (`packs.web_research.campaign`): `record_seed_queries_fn`,
`record_follow_up_queries_fn`, `scope_gate_for_query`,
`review_scope_amendment_fn`, `begin_research_round_fn` /
`perform_research_round` / `commit_research_round_fn`,
`pending_research_rounds_fn`, `campaign_config`, `STOP_REASONS`.

Search adapter (`packs.web_research.search_adapter`): `SearchRequest`,
`SearchOutcome`, `perform_neutral_search`, `provider_search_tools`,
`SUPPORTED_SEARCH_PROVIDERS`.

## Settings

`WebResearchSettings` makes every campaign bound configuration, never
hardcoded doctrine: `max_rounds`, `max_total_queries`, `max_pages`,
`max_follow_ups_per_round`, `max_findings_per_query`, `auto_follow_up`,
per-call timeout/token caps, the `min_new_urls_per_round` novelty floor,
`sensitive_topic_terms`, and `exclusions`. The plan proposal copies the
resolved values into its campaign disclosure (rounds, budgets, follow-up
policy, provider/model), so the owner approves exactly what will run; the
active connector operational policy caps pages and total provider calls.

## Constraints

Nothing here runs without a bound approved plan. Queries derive from
confirmed material only; scope expansion requires an explicit owner verdict;
budgets never yield to model enthusiasm; and page content never bypasses the
gateway.
