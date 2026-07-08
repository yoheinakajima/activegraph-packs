# Team/Ops Pack Changelog

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
- `project` object type: project grouping tasks and milestones
- `assignment` object type: task-to-principal assignment record
- `milestone` object type: grouping tasks with a target date and completion tracking
- `workload_estimate` object type: estimated workload per person per period
- `completion_evidence` object type: evidence that a task was completed
- `review_request` object type: review/approval request for a task
- Relation types: `assigned_to`, `task_depends_on`, `part_of_milestone`, `part_of_project`, `evidence_for`, `review_of`, `workload_for`
- `task_triager` behavior: promotes task_candidate observations to Core tasks
- `assignment_suggester` behavior: creates Assignments for tasks with owner_ref set
- `milestone_tracker` behavior: maintains open milestone registry (no graph.objects() call)
- `completion_verifier` behavior: creates CompletionEvidence from done/completed observations
- Module-level registries with `clear_team_ops_registry()` for fixture isolation
- Tools: `create_project`, `create_milestone`, `submit_task_candidate`, `assign_task`, `mark_task_done`
- Two fixtures covering task triage/assignment and completion workflow

### Design Notes
- Team/Ops wraps Core tasks via relations; does NOT replace the Core task primitive
- `task_triager` uses `category == 'task_candidate'` OR `metadata.task_candidate == True` OR `category == 'action_item'`
- `milestone_tracker` uses a module-level registry to avoid `graph.objects()` in behaviors
