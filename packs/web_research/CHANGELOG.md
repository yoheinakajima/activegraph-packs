# Web Research Pack Changelog

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
