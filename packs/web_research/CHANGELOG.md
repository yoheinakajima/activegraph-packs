# Web Research Pack Changelog

## 0.5.0 — the research understanding affordance (2026-07-14, ADR 0047)

- Declare `web_research_understanding`: public research joins governed
  campaigns through a typed affordance — R0 `model_search` within the
  owner-confirmed identity scope, `public_queries` outward disclosure, no
  raw drill-down (findings are the bounded reading surface), and a
  source-owned `outward_gate` wrapping the deterministic scope classifier
  over the CURRENT approved plan so a coordinator can never widen
  disclosure past what the owner approved.
- `record_coordinator_query_fn`: one coordinator-proposed query enters the
  running campaign's frontier under identical pre-registration rules —
  gated, recorded before execution, executed only by the next pump round.
- Zero-key fixture suite (`fixtures/run_fixtures.py`) covering the scope
  gates and the affordance declaration.

## 0.4.0 — bounded adaptive campaigns (2026-07-13, ADR 0045)

- Provider-neutral search adapter: one neutral request/result contract with
  explicit per-provider mappings (Anthropic's `web_search_20250305` tool
  block, OpenAI's `web_search` tool) — provider dialect lives in the adapter
  and nowhere else. Unsupported providers fail closed; model output is
  untrusted by default: parsed tolerantly, injection-scanned, bounded.
- An approved plan now runs as a bounded multi-round campaign: every query
  is recorded before it executes (seeds as the plan's strikeable surfaces,
  follow-ups as frontier rows with lineage — parent query, motivating
  findings, confirmed scope, round). A follow-up naming a new entity or a
  sensitive topic pauses as a reviewable scope amendment; research never
  silently broadens outward. Deterministic budgets (rounds, queries, pages,
  novelty floor) are authoritative over any model recommendation; each
  round's discovered pages ingest through the governed gateway; the stop
  reason is part of the run receipt. A commit replayed after a crash cannot
  double-append findings.
- The campaign disclosure (rounds, query/page budgets, follow-up policy,
  provider/model) is part of the plan the owner approves; `WebResearchSettings`
  makes every bound configuration, never hardcoded doctrine.

## 0.3.0 — honest response breadcrumbs (2026-07-13)

- Adopt the shared tolerant JSON parser and record bounded per-query
  response breadcrumbs on the run record.

## 0.2.0 — deferred execution, confirmed-fact queries (2026-07-13)

- Adopt the connector-control deferred plan execution seam
  (prepare/perform/commit), with settlement byte-identical to the
  synchronous path.
- Queries derive from promoted name/company facts plus the owner alias set,
  with the confirmed name+company pair leading; email addresses still never
  qualify.

## 0.1.0 — consented research (2026-07-13, ADR 0040)

- Research offers are ingestion plans: the queries are the plan's surfaces,
  each strikeable before approval, derived only from owner-confirmed subject
  facts plus caller-attested confirmed terms; email addresses never enter an
  outward query.
- Execution uses the configured model's server-side web search; discovered
  URLs ingest exclusively through the budgeted, recorded public-presence
  gateway, and findings join the existing "is this you?" verdict path.
- Full connector-control citizen: run observation, learning delta with
  planned-vs-actual, and a documents-family native view per run.
