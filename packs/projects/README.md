# Projects Pack

Projects are proposed from evidence and promoted by owner verdict
(ADR 0040 / D060). The derivation is deterministic and explainable, in
seed-priority order: owner-confirmed facts naming orgs/projects/affiliations,
entities recurring in owner-engaged communication, and presence/research
entities from owner-scoped evidence. The owner's connector taxonomy
(user-created labels) corroborates a proposal — a matching label joins its
sources and lifts its score — but never proposes by itself: labels map how
the owner uses a tool, not what their world is made of (ADR 0043). Every
candidate carries its sources and a human-readable rationale; derivation is
idempotent per normalized name (sources/score merge, confirmed and dismissed
candidates stay untouched). An LLM may describe a cluster via annotation; it
can never mint one.

Verdicts follow ADR 0020/0036 semantics: confirm (optionally renaming and/or
describing) mints a canonical `project`; dismiss is recorded; rename is
supersession. A `description` is evidence-backed working context, not
identity — confirm passes the candidate's description through (or an
explicit override), rename preserves it, and `describe_project_fn` edits an
active project's description as a patch with rationale, not a supersession.
Routing items into projects is the next slice.

## Objects

- `project_candidate` — a proposed project with explainable sources, kind,
  score, rationale, description, and a proposed/confirmed/dismissed/
  superseded lifecycle.
- `project` — an owner-confirmed canonical project (active/archived/
  superseded), linked to the candidate that seeded it.

## Public API

`derive_project_candidates_fn`, `review_project_candidate_fn` (confirm with
optional `name_override`/`description`, or dismiss), `rename_project_fn`,
`describe_project_fn`, `project_projects_fn`; registered tools:
`derive_project_candidates`, `review_project_candidate`, `rename_project`,
`project_projects`.

## Constraints

No settings schema — bounds are function arguments (`limit`). Candidates
never self-promote; the only path to a canonical project is an explicit
owner verdict, and supersession preserves history instead of rewriting it.
