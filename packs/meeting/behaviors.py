"""Meeting Pack behaviors — v0.1.

Behaviors:
  transcript_ingester     — source.created (kind=meeting_transcript) → Meeting + TranscriptSegments
  decision_extractor      — transcript_segment.created → MeetingDecision (if contains decision)
  action_item_extractor   — transcript_segment.created → MeetingActionItem + Core Task
  meeting_summarizer      — meeting.created → MeetingNote (summary)

All LLM behaviors use deterministic mock stubs in v0.1.

Transcript formats supported by transcript_ingester:
  1. JSON array:  [{"speaker": "...", "text": "...", "timestamp": 0.0}, ...]
  2. JSON object: {"segments": [...], "metadata": {...}}
  3. Structured text: "Speaker: text" lines (Zoom/Teams plain export)
  4. Plain text: paragraph/sentence splitting fallback

Registries:
  _MEETING_REGISTRY: source_id → meeting_id
  _MEETING_SEGMENT_COUNT: meeting_id → segment_count
  Call clear_meeting_registry() between test fixtures.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Optional

from activegraph.packs import behavior

from .settings import MeetingSettings

_MEETING_REGISTRY: dict[str, str] = {}
_MEETING_SEGMENT_COUNT: dict[str, int] = {}


def clear_meeting_registry() -> None:
    _MEETING_REGISTRY.clear()
    _MEETING_SEGMENT_COUNT.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_json_transcript(content: str) -> list[dict] | None:
    """Parse JSON transcript format (Zoom/Teams export).

    Accepted shapes:
      Array:  [{"speaker": "...", "text": "...", "timestamp": 0.0}, ...]
      Object: {"segments": [...], "title": "...", "date": "..."}
      Object: {"utterances": [...]}  (AssemblyAI / Deepgram style)

    Returns list of segment dicts, or None if content is not valid JSON
    or does not contain recognisable segment data.
    """
    stripped = content.strip()
    if not (stripped.startswith("[") or stripped.startswith("{")):
        return None
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None

    raw: list = []
    if isinstance(parsed, list):
        raw = parsed
    elif isinstance(parsed, dict):
        raw = (
            parsed.get("segments")
            or parsed.get("utterances")
            or parsed.get("turns")
            or []
        )

    if not raw:
        return None

    segments = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        text = item.get("text") or item.get("transcript") or item.get("content") or ""
        speaker = (
            item.get("speaker")
            or item.get("speaker_label")
            or item.get("name")
            or "Participant"
        )
        timestamp = item.get("timestamp") or item.get("start") or item.get("start_time") or 0.0
        if text:
            segments.append({
                "speaker": str(speaker),
                "text": str(text),
                "segment_index": i,
                "timestamp_seconds": float(timestamp) if timestamp else None,
            })
    return segments if segments else None


def _parse_structured_transcript(content: str) -> list[dict]:
    """Parse speaker-labelled transcript: 'Speaker: text' lines."""
    segments = []
    for i, line in enumerate(content.split("\n")):
        line = line.strip()
        if not line:
            continue
        match = re.match(r"^([A-Za-z][^:]{1,40}):\s*(.+)$", line)
        if match:
            segments.append({
                "speaker": match.group(1).strip(),
                "text": match.group(2).strip(),
                "segment_index": i,
            })
        elif segments:
            segments[-1]["text"] += " " + line
    return segments


def _parse_plain_transcript(content: str, max_segments: int) -> list[dict]:
    """Split plain text transcript into sentence-based segments."""
    sentences = re.split(r"(?<=[.!?])\s+", content.strip())
    segments = []
    for i, sentence in enumerate(sentences[:max_segments]):
        sentence = sentence.strip()
        if len(sentence.split()) < 3:
            continue
        segments.append({
            "speaker": "Participant",
            "text": sentence,
            "segment_index": i,
        })
    return segments


def _contains_decision(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def _contains_action_item(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def _extract_owner_ref(text: str, participants: list[str]) -> Optional[str]:
    """Try to find a participant name mentioned near ownership language."""
    text_lower = text.lower()
    for p in participants:
        if p.lower() in text_lower:
            return p
    return None


def _mock_summary(meeting_title: str, decisions: list[str], action_items: list[str]) -> str:
    lines = [f"# Meeting Summary: {meeting_title}\n"]
    if decisions:
        lines.append("## Decisions\n")
        for d in decisions:
            lines.append(f"- {d}")
    else:
        lines.append("## Decisions\nNo decisions recorded.\n")
    if action_items:
        lines.append("\n## Action Items\n")
        for a in action_items:
            lines.append(f"- {a}")
    else:
        lines.append("\n## Action Items\nNo action items recorded.\n")
    lines.append("\n## Notes\nSummary generated by meeting_summarizer (v0.1 mock).")
    return "\n".join(lines)


@behavior(
    name="transcript_ingester",
    on=["object.created"],
    where={"object.type": "source"},
    creates=["meeting", "transcript_segment"],
)
def transcript_ingester(event, graph, ctx, *, settings: MeetingSettings):
    """Ingest a meeting transcript source into Meeting + TranscriptSegment objects.

    On: object.created (source, kind=meeting_transcript)
    Creates: meeting, transcript_segment(s)
    Relations: derived_from_source(meeting → source), segment_of(segment → meeting)

    Handles both structured 'Speaker: text' and plain-text transcript formats.
    """
    obj = event.payload.get("object", {})
    source_id = obj.get("id")
    data = obj.get("data", {})

    if data.get("kind") != "meeting_transcript":
        return

    if source_id in _MEETING_REGISTRY:
        return

    meta = data.get("metadata") or {}
    content = data.get("content") or ""
    title = meta.get("title") or "Untitled Meeting"
    date = meta.get("date") or data.get("metadata", {}).get("received_at") or _now_iso()[:10]
    participants = meta.get("participants") or []
    platform = meta.get("platform") or "other"
    duration = meta.get("duration_minutes")

    # ── Create Meeting ─────────────────────────────────────────────────────
    try:
        meeting = graph.add_object("meeting", {
            "title": title,
            "date": date,
            "duration_minutes": duration,
            "platform": platform,
            "participants": participants,
            "source_id": source_id,
            "status": "completed",
        })
        meeting_id = meeting.id
        _MEETING_REGISTRY[source_id] = meeting_id
        _MEETING_SEGMENT_COUNT[meeting_id] = 0
        graph.add_relation(meeting_id, source_id, "derived_from_source")
    except Exception:
        return

    # ── Parse transcript — try formats in priority order ──────────────────
    # 1. JSON (Zoom/Teams/AssemblyAI/Deepgram export)
    # 2. Structured text "Speaker: text" lines
    # 3. Plain text sentence-splitting fallback
    raw_segments = (
        _parse_json_transcript(content)
        or (
            _parse_structured_transcript(content)
            if re.search(r"^[A-Za-z][^:]{1,40}:\s", content, re.MULTILINE)
            else None
        )
        or _parse_plain_transcript(content, settings.max_segments_per_meeting)
    )

    for seg_data in raw_segments[:settings.max_segments_per_meeting]:
        text = seg_data.get("text") or ""
        if len(text.split()) < settings.min_segment_words:
            continue

        has_decision = _contains_decision(text, settings.decision_keywords)
        has_action = _contains_action_item(text, settings.action_item_keywords)

        try:
            seg = graph.add_object("transcript_segment", {
                "meeting_id": meeting_id,
                "speaker": seg_data.get("speaker") or "Participant",
                "text": text,
                "segment_index": seg_data.get("segment_index") or 0,
                "timestamp_seconds": seg_data.get("timestamp_seconds"),
                "is_decision": has_decision,
                "is_action_item": has_action,
            })
            graph.add_relation(seg.id, meeting_id, "segment_of")
            _MEETING_SEGMENT_COUNT[meeting_id] = _MEETING_SEGMENT_COUNT.get(meeting_id, 0) + 1
        except Exception:
            pass


@behavior(
    name="decision_extractor",
    on=["object.created"],
    where={"object.type": "transcript_segment"},
    creates=["meeting_decision"],
)
def decision_extractor(event, graph, ctx, *, settings: MeetingSettings):
    """Extract MeetingDecision objects from flagged transcript segments.

    On: object.created (transcript_segment, is_decision=True)
    Creates: meeting_decision
    Relations: decision_in(decision → meeting), decision_from_segment(decision → segment)
    """
    obj = event.payload.get("object", {})
    seg_id = obj.get("id")
    data = obj.get("data", {})

    if not data.get("is_decision"):
        return

    meeting_id = data.get("meeting_id")
    text = data.get("text") or ""
    speaker = data.get("speaker") or "Participant"

    try:
        decision = graph.add_object("meeting_decision", {
            "meeting_id": meeting_id,
            "text": text,
            "segment_id": seg_id,
            "decided_by": [speaker],
            "confidence": 0.80,
        })
        if meeting_id:
            graph.add_relation(decision.id, meeting_id, "decision_in")
        graph.add_relation(decision.id, seg_id, "decision_from_segment")
    except Exception:
        pass


@behavior(
    name="action_item_extractor",
    on=["object.created"],
    where={"object.type": "transcript_segment"},
    creates=["meeting_action_item", "task"],
)
def action_item_extractor(event, graph, ctx, *, settings: MeetingSettings):
    """Extract MeetingActionItems and create Core Tasks from transcript segments.

    On: object.created (transcript_segment, is_action_item=True)
    Creates: meeting_action_item, task (Core)
    Relations: action_item_in, action_item_from_segment, action_creates_task
    """
    obj = event.payload.get("object", {})
    seg_id = obj.get("id")
    data = obj.get("data", {})

    if not data.get("is_action_item"):
        return

    if not settings.auto_create_tasks_from_action_items:
        return

    meeting_id = data.get("meeting_id")
    text = data.get("text") or ""
    speaker = data.get("speaker") or "Participant"

    action_text = text[:200] if text else "Action item from meeting"

    try:
        task = graph.add_object("task", {
            "title": f"[Meeting] {action_text[:60]}",
            "description": action_text,
            "status": "candidate",
            "priority": "medium",
        })

        action_item = graph.add_object("meeting_action_item", {
            "meeting_id": meeting_id,
            "text": action_text,
            "owner_ref": speaker if speaker != "Participant" else None,
            "segment_id": seg_id,
            "task_id": task.id,
            "status": "open",
        })

        if meeting_id:
            graph.add_relation(action_item.id, meeting_id, "action_item_in")
        graph.add_relation(action_item.id, seg_id, "action_item_from_segment")
        graph.add_relation(action_item.id, task.id, "action_creates_task")
    except Exception:
        pass


@behavior(
    name="meeting_summarizer",
    on=["object.created"],
    where={"object.type": "meeting"},
    creates=["meeting_note"],
)
def meeting_summarizer(event, graph, ctx, *, settings: MeetingSettings):
    """Generate a MeetingNote summary when a meeting is ingested.

    On: object.created (meeting)
    Creates: meeting_note (note_type=summary), artifact (Core)
    Relations: note_for(note → meeting)

    v0.1: mock summary from title metadata — runs immediately on meeting.created.
    Full summary with decisions/action items requires a second pass after segments
    are processed; the note is patched by note_patcher if needed.
    """
    if not settings.auto_summarize_meeting:
        return

    obj = event.payload.get("object", {})
    meeting_id = obj.get("id")
    data = obj.get("data", {})

    title = data.get("title") or "Meeting"
    participants = data.get("participants") or []

    summary_content = (
        f"# Meeting Summary: {title}\n\n"
        f"**Date:** {data.get('date') or 'Unknown'}\n"
        f"**Platform:** {data.get('platform') or 'Unknown'}\n"
        f"**Participants:** {', '.join(participants) if participants else 'Unknown'}\n\n"
        "## Summary\n"
        "Transcript is being processed. Decisions and action items will be extracted "
        "from individual segments as they are created.\n\n"
        "*(v0.1 mock summary — real LLM summarization in v0.2)*"
    )

    try:
        note = graph.add_object("meeting_note", {
            "meeting_id": meeting_id,
            "content": summary_content,
            "note_type": "summary",
        })
        graph.add_relation(note.id, meeting_id, "note_for")
    except Exception:
        pass


BEHAVIORS = [
    transcript_ingester,
    decision_extractor,
    action_item_extractor,
    meeting_summarizer,
]
