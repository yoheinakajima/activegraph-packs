# Meeting Pack Changelog

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
- `meeting` object type: meeting with participants, platform, and status
- `transcript_segment` object type: one speaker turn, flagged for decisions/action items
- `meeting_decision` object type: decision extracted from transcript with confidence
- `meeting_action_item` object type: action item linked to a Core task
- `meeting_note` object type: meeting summary or notes (summary, minutes, highlights)
- Relation types: `segment_of`, `decision_in`, `action_item_in`, `note_for`, `decision_from_segment`, `action_item_from_segment`, `action_creates_task`, `derived_from_source`
- `transcript_ingester` behavior: parses structured and plain-text transcripts into Meeting + segments
- `decision_extractor` behavior: creates MeetingDecision from is_decision-flagged segments
- `action_item_extractor` behavior: creates MeetingActionItem + Core task from is_action_item segments
- `meeting_summarizer` behavior: creates MeetingNote summary on meeting creation
- Module-level registries with `clear_meeting_registry()` for fixture isolation
- Tools: `ingest_transcript`, `create_meeting`, `add_decision`, `add_action_item`
- Two fixtures covering transcript ingestion pipeline and manual meeting workflow

### Design Notes
- Structured transcript format (`Speaker: text`) is auto-detected by regex
- Keyword matching for decisions/action items is fully configurable via `MeetingSettings`
- `meeting_summarizer` creates a placeholder summary immediately; full content from post-processing in v0.2
- Core tasks created from action items carry `owner_ref` and `due_at` from the action item
