"""Meeting Pack fixtures — v0.1.

Fixture 1: transcript_ingestion_pipeline
  A meeting_transcript source is created. transcript_ingester fires → Meeting + TranscriptSegments.
  decision_extractor fires on decision-flagged segments → MeetingDecision.
  action_item_extractor fires on action-flagged segments → MeetingActionItem + Core tasks.
  meeting_summarizer fires → MeetingNote.

Fixture 2: manual_meeting_workflow
  A meeting is created directly. Decisions and action items are added via tools.
  Verifies tool-based workflow (no transcript).

Run:
    python packs/meeting/fixtures/run_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

from activegraph import Graph, Runtime
from packs.core import pack as core_pack, CoreSettings
from packs.meeting import pack as meeting_pack, MeetingSettings
from packs.meeting.behaviors import clear_meeting_registry
from packs.meeting.tools import (
    ingest_transcript_fn,
    create_meeting_fn,
    add_decision_fn,
    add_action_item_fn,
)


SAMPLE_TRANSCRIPT = """Alice: Good morning everyone. Let's get started with the sprint review.
Bob: We completed the database migration last week. All tests are passing.
Alice: Great work. We decided to move to PostgreSQL 16 for all new services going forward.
Carol: I'll follow up with the infrastructure team about the upgrade timeline.
Bob: Action item: Bob will write the migration runbook by next Friday.
Alice: We also need to update the API documentation. Carol, can you take that on?
Carol: Sure, I'll have that done by end of week.
Alice: One more thing — we decided to adopt feature flags for all new releases going forward.
Bob: I'll set up the LaunchDarkly integration by Thursday. That's my action item.
Alice: Perfect. Let's wrap up. Summary: migration done, feature flags incoming, docs update in progress.
"""


def run_transcript_ingestion_pipeline() -> bool:
    print("\n" + "=" * 60)
    print("Fixture: transcript_ingestion_pipeline")
    print("  source → Meeting + Segments → Decisions + ActionItems + Note")
    print("=" * 60)

    clear_meeting_registry()

    graph = Graph()
    rt = Runtime(graph)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(meeting_pack, settings=MeetingSettings(
        auto_create_tasks_from_action_items=True,
        auto_summarize_meeting=True,
        min_segment_words=4,
    ))

    source = ingest_transcript_fn(
        graph,
        title="Sprint Review — Week 23",
        content=SAMPLE_TRANSCRIPT,
        date="2026-06-03",
        participants=["Alice", "Bob", "Carol"],
        platform="zoom",
        duration_minutes=30,
    )
    rt.run_until_idle()

    meetings = list(graph.objects(type="meeting"))
    segments = list(graph.objects(type="transcript_segment"))
    decisions = list(graph.objects(type="meeting_decision"))
    action_items = list(graph.objects(type="meeting_action_item"))
    notes = list(graph.objects(type="meeting_note"))
    tasks = list(graph.objects(type="task"))

    print(f"\n  After transcript ingestion:")
    print(f"  meetings:            {len(meetings)}")
    for m in meetings:
        print(f"    '{m.data.get('title')}' date={m.data.get('date')} "
              f"platform={m.data.get('platform')}")
    print(f"  transcript_segments: {len(segments)}")
    decision_segs = [s for s in segments if s.data.get("is_decision")]
    action_segs = [s for s in segments if s.data.get("is_action_item")]
    print(f"    flagged as decision:     {len(decision_segs)}")
    print(f"    flagged as action_item:  {len(action_segs)}")
    print(f"  meeting_decisions:   {len(decisions)}")
    for d in decisions:
        print(f"    conf={d.data.get('confidence'):.2f} '{d.data.get('text')[:60]}'")
    print(f"  meeting_action_items: {len(action_items)}")
    for a in action_items:
        print(f"    owner={a.data.get('owner_ref')} task_id={'set' if a.data.get('task_id') else 'none'}")
        print(f"    '{a.data.get('text')[:60]}'")
    print(f"  meeting_notes:        {len(notes)}")
    print(f"  core tasks (from AIs): {len(tasks)}")

    all_rels = list(graph.relations())
    rel_types = {r.type for r in all_rels}
    print(f"\n  relation types seen: {sorted(rel_types)}")

    failures = []
    if not meetings:
        failures.append("transcript_ingester created no Meeting objects")
    if not segments:
        failures.append("transcript_ingester created no TranscriptSegment objects")
    if not decisions:
        failures.append("decision_extractor created no MeetingDecision objects")
    if not action_items:
        failures.append("action_item_extractor created no MeetingActionItem objects")
    if not notes:
        failures.append("meeting_summarizer created no MeetingNote objects")
    if not tasks:
        failures.append("action_item_extractor did not create Core tasks")
    if "segment_of" not in rel_types:
        failures.append("Missing relation: segment_of")
    if "decision_in" not in rel_types:
        failures.append("Missing relation: decision_in")
    if "action_item_in" not in rel_types:
        failures.append("Missing relation: action_item_in")
    if "action_creates_task" not in rel_types:
        failures.append("Missing relation: action_creates_task")
    if "note_for" not in rel_types:
        failures.append("Missing relation: note_for")

    if failures:
        print("  FAIL:")
        for f in failures:
            print(f"    {f}")
        return False

    print("  PASS")
    return True


def run_manual_meeting_workflow() -> bool:
    print("\n" + "=" * 60)
    print("Fixture: manual_meeting_workflow")
    print("  create meeting → add decisions + action items via tools")
    print("=" * 60)

    clear_meeting_registry()

    graph = Graph()
    rt = Runtime(graph)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(meeting_pack, settings=MeetingSettings(
        auto_create_tasks_from_action_items=True,
        auto_summarize_meeting=True,
    ))

    # Create meeting directly
    meeting = create_meeting_fn(
        graph,
        title="Quarterly Planning — Q3 2026",
        date="2026-07-01",
        participants=["CEO", "CTO", "Head of Product"],
        platform="google_meet",
        duration_minutes=90,
    )
    rt.run_until_idle()

    # Note is auto-generated by meeting_summarizer
    notes_before = list(graph.objects(type="meeting_note"))
    print(f"\n  After meeting creation:")
    print(f"  meetings:      1 (id={meeting.id[:8]})")
    print(f"  meeting_notes: {len(notes_before)} (from meeting_summarizer)")

    # Add decisions manually
    decision1 = add_decision_fn(
        graph,
        meeting_id=meeting.id,
        text="We decided to prioritize the mobile app rewrite for Q3.",
        decided_by=["CEO", "CTO"],
        confidence=0.95,
    )
    rt.run_until_idle()

    decision2 = add_decision_fn(
        graph,
        meeting_id=meeting.id,
        text="Marketing budget increased by 20% for Q3 campaigns.",
        decided_by=["CEO"],
        confidence=0.90,
    )
    rt.run_until_idle()

    # Add action items manually
    ai1 = add_action_item_fn(
        graph,
        meeting_id=meeting.id,
        text="CTO to produce technical spec for mobile app rewrite by July 15",
        owner_ref="cto@acme.com",
        due_at="2026-07-15",
        create_task=True,
    )
    rt.run_until_idle()

    ai2 = add_action_item_fn(
        graph,
        meeting_id=meeting.id,
        text="Head of Product to define Q3 OKRs by July 8",
        owner_ref="product@acme.com",
        due_at="2026-07-08",
        create_task=True,
    )
    rt.run_until_idle()

    decisions = list(graph.objects(type="meeting_decision"))
    action_items = list(graph.objects(type="meeting_action_item"))
    tasks = list(graph.objects(type="task"))
    notes = list(graph.objects(type="meeting_note"))

    print(f"\n  After adding decisions + action items:")
    print(f"  meeting_decisions:    {len(decisions)}")
    for d in decisions:
        print(f"    conf={d.data.get('confidence'):.2f} by={d.data.get('decided_by')} '{d.data.get('text')[:60]}'")
    print(f"  meeting_action_items: {len(action_items)}")
    for a in action_items:
        print(f"    owner={a.data.get('owner_ref')} due={a.data.get('due_at', '')[:10]}")
        print(f"    task_id={'set' if a.data.get('task_id') else 'none'} '{a.data.get('text')[:50]}'")
    print(f"  core tasks: {len(tasks)}")
    print(f"  notes:      {len(notes)}")

    all_rels = list(graph.relations())
    rel_types = {r.type for r in all_rels}
    print(f"\n  relation types seen: {sorted(rel_types)}")

    failures = []
    if len(decisions) < 2:
        failures.append(f"Expected 2 decisions, got {len(decisions)}")
    if len(action_items) < 2:
        failures.append(f"Expected 2 action items, got {len(action_items)}")
    if len(tasks) < 2:
        failures.append(f"Expected 2 tasks from action items, got {len(tasks)}")
    task_with_owner = [t for t in tasks if t.data.get("owner_ref")]
    if not task_with_owner:
        failures.append("Tasks should have owner_ref from action item")
    if not notes:
        failures.append("meeting_summarizer did not create MeetingNote")
    if "decision_in" not in rel_types:
        failures.append("Missing relation: decision_in")
    if "action_item_in" not in rel_types:
        failures.append("Missing relation: action_item_in")
    if "action_creates_task" not in rel_types:
        failures.append("Missing relation: action_creates_task")
    if "note_for" not in rel_types:
        failures.append("Missing relation: note_for")

    if failures:
        print("  FAIL:")
        for f in failures:
            print(f"    {f}")
        return False

    print("  PASS")
    return True


def run_all() -> None:
    results = [
        run_transcript_ingestion_pipeline(),
        run_manual_meeting_workflow(),
    ]
    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"Meeting Pack: {passed}/{total} fixtures passed")
    print("=" * 60 + "\n")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    run_all()
