# Projects Pack Changelog

## 0.5.0 — owner-authored workstreams + the evidence router (2026-07-17, Mission Control vertical)

- `create_workstream_fn` / `@tool create_workstream`: the owner-authored
  door beside the candidate flow — a canonical `project` recorded honestly
  as `derivation_kind: owner_authored` (no candidate, no pretended
  evidence), with goal/dates under `metadata.ops`. Idempotent per active
  name. team_ops' `create_project_fn` now delegates here (one mint site,
  D062).
- `archive_project_fn` / `@tool archive_project`: status transition with
  the owner's reason in the receipt; routes and relations stay reachable.
- `describe_project_fn` registered as `@tool describe_project`.
- **`router.py` — the evidence→workstream router** (GAP 0's routing half):
  `derive_route_fn` scores every active workstream for one item from
  named deterministic signals (participant-entity associations, project
  name/confirmed-alias mentions, the owner's own labels, description
  token overlap, and recorded owner corrections toward/away). One
  confident winner (≥600 milli with ≥150 margin) routes automatically —
  reversible, receipted, correctable; anything ambiguous stays honestly
  `unfiled`; an owner unroute pins the item unfiled. `route_pending_fn`
  is the bounded, idempotent, replay-stable batch pass;
  `unrouted_items_fn` is the honest Unfiled tray read. No model output
  participates in any decision.
- `route_item_fn` gains optional `confidence_milli` recorded on the
  `routed_to` relation for derived routes.

## 0.4.0 — the work graph (2026-07-14, ADR 0049)

- Typed relations: `workstream_contains` (cycle-safe DAG, multiple parents
  legal, no stored depth limit), `workstream_associated_with` (entities
  associate with roles — never converted into projects),
  `workstream_depends_on` / `workstream_related_to`, `routed_to` (items and
  evidence with recorded routing provenance), and `classified_by` over
  governed `project_facet`s.
- `link_workstreams_fn` rejects cycles with the exact explainable ancestor
  path; `descendants_fn` traverses with explicit depth/item bounds and
  reports truncation honestly.
- Organizational views (`organizational_view`): versioned governed
  perspectives (roots, grouping rules, ordering, labels, primary display
  paths) with propose → owner promote/edit/reject → supersede semantics.
  An owner's hierarchy lives as promoted graph state, never pack code.
- `project_context_packet_fn`: the provenance-bearing, bounded
  graph-reachability context for one workstream — associations, confirmed
  aliases, routed items, people, included/excluded refs, and traversal
  bounds. Exact-name matching appears nowhere.
- `correct_routing_fn`: owner re-files are durable `routing_correction`
  evidence for the prediction loop, and the packet changes predictably.
- Zero-key fixture suite extended to the graph seams.

## 0.3.0 — evidence-backed descriptions (2026-07-13, ADR 0046)

- Candidates and projects carry an evidence-backed `description`: working
  context, not identity. Confirm passes the candidate's description through
  (or an explicit override), rename preserves it, and `describe_project_fn`
  is the owner edit of an active project's description — a patch with
  rationale, not a supersession.

## 0.2.0 — labels corroborate, never propose (2026-07-13)

- The owner's connector taxonomy (user labels) now corroborates candidates
  seeded elsewhere instead of proposing its own: live dogfood showed labels
  map tool usage, not the world.

## 0.1.0 — evidence-derived projects (2026-07-13, ADR 0040)

- Deterministic, explainable project candidates in seed-priority order:
  owner-confirmed facts, the owner's own connector taxonomy (user labels),
  entities recurring in communication, presence/research entities. Every
  candidate carries sources and a human-readable rationale.
- Owner verdicts promote: confirm (optionally renaming) mints a canonical
  project; dismiss is recorded; rename is supersession. Routing is the
  next slice.
