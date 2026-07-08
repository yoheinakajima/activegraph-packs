# VC Pack Changelog

## v0.1.1 — Relation integrity fix (2026-07-08)

### Fixed
- **`add_relation` argument order.** Relation writes passed
  `(type, source, target)` but the API is `(source, target, type)` — the same
  bug the Chat Pack fixed in its v0.2.0. Affected relations were being written
  as garbage edges (the type string as the source id), silently breaking graph
  traversal over this pack's audit trail. Part of a repo-wide sweep (80 calls
  across 14 packs) that also corrected fixture assertions written against the
  broken shape (`r.source` where `r.type` was meant).

## v0.1.0 — 2026-06-03

### Added
- `company_profile` object type: startup under evaluation
- `founder_profile` object type: founder with email, LinkedIn, company link
- `deal_round` object type: fundraising round with status tracking
- `traction_metric` object type: ARR, MRR, DAU, NPS, and custom metrics
- `investment_memo` object type: structured memo linked to Core artifact
- `investment_thesis` object type: fund's investment thesis
- `deal_risk` object type: categorized risk with severity and mitigation
- `followup` object type: follow-up item linked to Core task
- `lp_update` object type: portfolio update for limited partners
- Relation types: `founded_by`, `raised_in`, `reports_metric`, `memo_for`, `risk_in`, `followup_for`, `founder_outreach_source`, `derived_from_comm`
- `founder_email_detector` behavior: keyword-based founder outreach detection
- `company_enricher` behavior: CompanyProfile + FounderProfile from outreach observations
- `memo_drafter` behavior: template-based InvestmentMemo draft on company creation
- `followup_tracker` behavior: Followup + Core task on company creation
- `lp_update_generator` behavior: LP update draft for term_sheet/closing/closed deals
- Module-level registries with `clear_vc_registry()` for fixture isolation
- Tools: `ingest_founder_email`, `create_deal_round`, `add_traction_metric`, `add_deal_risk`
- Two fixtures covering founder outreach pipeline and deal round tracking

### Notes
- All LLM behaviors use deterministic mock stubs in v0.1
- Real LLM-powered company enrichment and memo drafting planned for v0.2
- memo_drafter creates two investment_memo objects (known v0.1 behavior — first is artifact-only, second is the linked memo); will be fixed in v0.2
