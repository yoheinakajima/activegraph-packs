"""activegraph.packs.meeting — Meeting Pack v0.1.

Meeting ingestion, transcript processing, decision extraction, and action item creation.

Object types:
  meeting              — Meeting with participants, platform, and transcript
  transcript_segment   — A segment of a meeting transcript (one speaker turn)
  meeting_decision     — A decision made during a meeting
  meeting_action_item  — An action item with a link to a Core task
  meeting_note         — Meeting summary or notes

Behaviors:
  transcript_ingester    — source.created (kind=meeting_transcript) → Meeting + TranscriptSegments
  decision_extractor     — transcript_segment.created (is_decision=True) → MeetingDecision
  action_item_extractor  — transcript_segment.created (is_action_item=True) → MeetingActionItem + Core task
  meeting_summarizer     — meeting.created → MeetingNote (summary)

Key integration: action_item_extractor creates Core tasks from action items,
which flow into Team/Ops Pack for assignment and milestone tracking.

Composes with: Core Pack (task from action items), Team/Ops Pack, Identity Pack
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import MeetingSettings
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# requires=["core"], composes_with=["team_ops", "identity_auth", "communication"]
pack = Pack(
    name="meeting",
    version="0.1.1",
    description=(
        "Meeting ingestion and processing: transcript parsing (structured and plain text), "
        "decision extraction, action item extraction with Core task creation, "
        "and meeting summarization. Provides 5 meeting-domain object types."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=load_prompts_from_dir(_PROMPTS_DIR) if _PROMPTS_DIR.exists() else (),
    settings_schema=MeetingSettings,
)

__all__ = ["pack", "MeetingSettings"]
