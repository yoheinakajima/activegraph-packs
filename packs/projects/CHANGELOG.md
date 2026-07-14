# Projects Pack Changelog

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
