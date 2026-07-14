"""Team/Ops Pack fixtures — v0.1.

Fixture 1: task_triage_and_assignment
  task_candidate observations are created.
  task_triager fires → Core task objects.
  assignment_suggester fires → Assignment objects.
  Milestone and project are created for context.

Fixture 2: completion_workflow
  Tasks are created. A milestone is set up.
  A completion observation fires completion_verifier → CompletionEvidence.

Run:
    python packs/team_ops/fixtures/run_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

from activegraph import Graph, Runtime
from packs.core import pack as core_pack, CoreSettings
from packs.projects import pack as projects_pack
from packs.team_ops import pack as team_ops_pack, TeamOpsSettings
from packs.team_ops.behaviors import clear_team_ops_registry
from packs.team_ops.tools import (
    create_project_fn,
    create_milestone_fn,
    submit_task_candidate_fn,
    assign_task_fn,
    mark_task_done_fn,
)


def run_task_triage_and_assignment() -> bool:
    print("\n" + "=" * 60)
    print("Fixture: task_triage_and_assignment")
    print("  observations → task_triager → tasks | assignment_suggester → assignments")
    print("=" * 60)

    clear_team_ops_registry()

    graph = Graph()
    rt = Runtime(graph)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(projects_pack)
    rt.load_pack(team_ops_pack, settings=TeamOpsSettings(
        auto_assign_tasks=True,
        default_capacity_hours_per_week=40.0,
    ))

    # Create a project and milestone first
    project = create_project_fn(
        graph,
        name="Q3 Infrastructure",
        description="Infrastructure improvements for Q3",
        goal="Reduce p99 latency by 30%",
        owner_ref="eng-lead@acme.com",
        target_date="2026-09-30",
    )
    rt.run_until_idle()

    milestone = create_milestone_fn(
        graph,
        project_id=project.id,
        title="Phase 1: Database optimization",
        description="Optimize slow queries and add caching layer",
        target_date="2026-07-15",
    )
    rt.run_until_idle()

    projects = list(graph.objects(type="project"))
    milestones = list(graph.objects(type="milestone"))
    print(f"\n  After setup:")
    print(f"  projects:   {len(projects)}")
    print(f"  milestones: {len(milestones)}")

    # Submit task candidates
    submit_task_candidate_fn(
        graph,
        text="Optimize slow SQL queries in user dashboard assigned to @alice@acme.com",
        owner_ref="alice@acme.com",
        project_id=project.id,
        milestone_id=milestone.id,
        priority="high",
    )
    rt.run_until_idle()

    submit_task_candidate_fn(
        graph,
        text="Add Redis caching layer for session data — assign to @bob@acme.com",
        owner_ref="bob@acme.com",
        project_id=project.id,
        priority="medium",
    )
    rt.run_until_idle()

    submit_task_candidate_fn(
        graph,
        text="Write action item: update runbook with new deployment steps by Friday",
        owner_ref=None,
        priority="low",
    )
    rt.run_until_idle()

    tasks = list(graph.objects(type="task"))
    assignments = list(graph.objects(type="assignment"))
    observations = list(graph.objects(type="observation"))

    print(f"\n  After submitting 3 task candidates:")
    print(f"  observations:  {len(observations)}")
    print(f"  core tasks:    {len(tasks)}")
    for t in tasks:
        print(f"    [{t.data.get('status')}|{t.data.get('priority')}] '{t.data.get('title')[:60]}'")
    print(f"  assignments:   {len(assignments)}")
    for a in assignments:
        print(f"    task_id={a.data.get('task_id')[:8]} principal={a.data.get('principal_ref')}")

    all_rels = list(graph.relations())
    rel_types = {r.type for r in all_rels}
    print(f"\n  relation types seen: {sorted(rel_types)}")

    failures = []
    if not projects:
        failures.append("No Project created")
    if not milestones:
        failures.append("No Milestone created — milestone_tracker did not fire")
    task_candidates = [o for o in observations if o.data.get("category") == "task_candidate"]
    if len(task_candidates) < 3:
        failures.append(f"Expected 3 task_candidate observations, got {len(task_candidates)}")
    if not tasks:
        failures.append("task_triager created no Core tasks")
    assigned_tasks = [a for a in assignments if a.data.get("principal_ref")]
    if not assigned_tasks:
        failures.append("assignment_suggester created no assignments")
    if "part_of_project" not in rel_types:
        failures.append("Missing relation: part_of_project")
    if "assigned_to" not in rel_types:
        failures.append("Missing relation: assigned_to")

    if failures:
        print("  FAIL:")
        for f in failures:
            print(f"    {f}")
        return False

    print("  PASS")
    return True


def run_completion_workflow() -> bool:
    print("\n" + "=" * 60)
    print("Fixture: completion_workflow")
    print("  task creation → manual assignment → completion evidence → task done")
    print("=" * 60)

    clear_team_ops_registry()

    graph = Graph()
    rt = Runtime(graph)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(projects_pack)
    rt.load_pack(team_ops_pack, settings=TeamOpsSettings(
        auto_assign_tasks=False,
    ))

    # Create a task directly
    task = graph.add_object("task", {
        "title": "Deploy new caching service to staging",
        "description": "Deploy Redis cache to staging environment and run smoke tests",
        "status": "active",
        "priority": "high",
        "owner_ref": "devops@acme.com",
    })
    rt.run_until_idle()

    # Manually assign the task
    assignment = assign_task_fn(graph, task.id, "devops@acme.com")
    rt.run_until_idle()

    assignments = list(graph.objects(type="assignment"))
    print(f"\n  After task creation + manual assignment:")
    print(f"  tasks:       1")
    print(f"  assignments: {len(assignments)}")

    # Create completion evidence via direct tool (bypasses completion_verifier)
    evidence = mark_task_done_fn(
        graph,
        task_id=task.id,
        evidence_text="Deployed Redis 7.0 to staging. Smoke tests passed. Cache hit rate: 94%.",
        completed_by_ref="devops@acme.com",
    )
    rt.run_until_idle()

    completion_evidences = list(graph.objects(type="completion_evidence"))
    updated_task = graph.get_object(task.id)

    print(f"\n  After completion:")
    print(f"  completion_evidences: {len(completion_evidences)}")
    for e in completion_evidences:
        print(f"    by={e.data.get('completed_by_ref')} '{e.data.get('evidence_text')[:60]}'")
    if updated_task:
        print(f"  task status: {updated_task.data.get('status')} (should be 'done')")

    # Also test completion_verifier behavior: create observation with task_id
    graph.add_object("observation", {
        "text": "Task completed successfully by devops@acme.com — all tests passed",
        "confidence": 0.9,
        "category": "decision",
        "metadata": {
            "task_id": task.id,
            "completed_by_ref": "devops@acme.com",
        },
    })
    rt.run_until_idle()

    all_evidences = list(graph.objects(type="completion_evidence"))
    print(f"\n  After completion observation (completion_verifier):")
    print(f"  completion_evidences total: {len(all_evidences)}")

    all_rels = list(graph.relations())
    rel_types = {r.type for r in all_rels}
    print(f"\n  relation types seen: {sorted(rel_types)}")

    failures = []
    if not completion_evidences:
        failures.append("No CompletionEvidence created by mark_task_done_fn")
    if updated_task and updated_task.data.get("status") != "done":
        failures.append(f"Task status should be 'done', got {updated_task.data.get('status')}")
    if "evidence_for" not in rel_types:
        failures.append("Missing relation: evidence_for")
    if "assigned_to" not in rel_types:
        failures.append("Missing relation: assigned_to")
    if len(all_evidences) < 2:
        failures.append("completion_verifier should also create a CompletionEvidence from observation")

    if failures:
        print("  FAIL:")
        for f in failures:
            print(f"    {f}")
        return False

    print("  PASS")
    return True


def run_all() -> None:
    results = [
        run_task_triage_and_assignment(),
        run_completion_workflow(),
    ]
    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"Team/Ops Pack: {passed}/{total} fixtures passed")
    print("=" * 60 + "\n")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    run_all()
