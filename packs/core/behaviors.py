"""Core Pack behaviors — v0.1.

Three lightweight deterministic behaviors. No LLM calls, no external tools.

1. observation_extractor — on source.created, extracts key observations
   from source content using heuristics (sentence splitting + keyword
   scoring). Production packs would use llm_behavior for richer
   extraction; this gives the system a working baseline.

2. task_linker — on observation.created, links the observation to any
   existing open tasks whose title overlaps significantly with the
   observation text. Creates a 'produces' relation.

3. memory_candidate_proposer — on observation.created, proposes memory
   candidates for high-confidence preference/decision/fact observations.

Design rules:
- description belongs in the function docstring (not in @behavior)
- @behavior signature: (name, on, where, view, creates, budget, priority)
- @llm_behavior signature additionally accepts: description, output_schema, etc.
"""

from __future__ import annotations

import re
from typing import Optional

from activegraph.packs import behavior

from .object_types import Observation, MemoryCandidate
from .settings import CoreSettings


# ------------------------------------------------------------------ helpers


def _sentences(text: str) -> list[str]:
    """Split text into sentences, trimmed, non-empty, min 10 chars."""
    raw = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s.strip() for s in raw if len(s.strip()) >= 10]


def _word_set(text: str) -> set[str]:
    """Lowercase word set, stripping punctuation, ignoring stopwords."""
    STOPWORDS = {
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to",
        "for", "of", "with", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "shall", "can",
        "this", "that", "these", "those", "it", "its", "i", "we", "you",
        "he", "she", "they", "them", "their", "our", "your", "my", "not",
    }
    words = re.findall(r"[a-z]+", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def _overlap_ratio(a: str, b: str) -> float:
    """Jaccard overlap of word sets between two strings (0.0–1.0)."""
    wa, wb = _word_set(a), _word_set(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _infer_category(text: str) -> Optional[str]:
    """Infer an observation category from keyword patterns.

    Returns one of: preference, instruction, decision, action_item,
    risk, fact, question, intent — or None if no pattern matches.

    Priority order matters: more specific patterns are checked first.
    """
    text_lower = text.lower()

    # Preference — explicit "prefer", "rather", "instead of"
    if any(w in text_lower for w in ("prefer", "rather", "instead of", "favourite", "favorite")):
        return "preference"

    # Instruction — directives someone should follow
    if any(w in text_lower for w in ("should", "must", "always", "never", "make sure", "ensure", "please", "requirement", "required")):
        return "instruction"

    # Decision — past resolutions
    if any(w in text_lower for w in ("decide", "decided", "agreed", "confirmed", "resolved", "chosen", "chose")):
        return "decision"

    # Action item — concrete to-do
    if any(w in text_lower for w in ("todo", "to-do", "to do", "action item", "follow up", "follow-up")):
        return "action_item"

    # Risk — problems or concerns
    if any(w in text_lower for w in ("risk", "concern", "problem", "issue", "fail", "failure", "danger", "critical")):
        return "risk"

    # Fact — reported statements
    if any(w in text_lower for w in ("says", "said", "mentioned", "noted", "stated", "reported", "according")):
        return "fact"

    # Question
    if "?" in text:
        return "question"

    # Generic intent (wants/needs)
    if any(w in text_lower for w in ("want", "need", "ask", "request")):
        return "intent"

    return None


# ------------------------------------------------------------------ behaviors


@behavior(
    name="observation_extractor",
    on=["object.created"],
    where={"object.type": "source"},
    creates=["observation"],
)
def observation_extractor(event, graph, ctx, *, settings: CoreSettings):
    """Extract key observations from a newly created source object.

    Uses sentence splitting and heuristic scoring to identify the most
    informative sentences as observations. Deterministic — no LLM needed.

    On: object.created (source)
    Creates: observation, grounds(source → observation)
    """
    obj = event.payload.get("object", {})
    source_id = obj.get("id")
    source_data = obj.get("data", {})
    content = source_data.get("content", "")
    frame_id = source_data.get("frame_id")

    if not content or not source_id:
        return

    sentences = _sentences(content)
    if not sentences:
        return

    # Score sentences: prefer longer informative sentences.
    word_sets = [_word_set(s) for s in sentences]
    max_words = max((len(ws) for ws in word_sets), default=1)

    scored = sorted(
        zip(sentences, word_sets),
        key=lambda x: len(x[1]) / max_words,
        reverse=True,
    )

    limit = min(settings.max_observations_per_source, len(scored))
    created = 0

    for sentence, ws in scored[:limit]:
        if created >= limit:
            break

        confidence = round(len(ws) / max_words, 2)
        low_confidence = confidence < settings.observation_min_confidence
        category = _infer_category(sentence)

        obs = graph.add_object(
            "observation",
            Observation(
                text=sentence,
                confidence=confidence,
                low_confidence=low_confidence,
                source_ids=[source_id],
                frame_id=frame_id,
                category=category,
            ).model_dump(),
        )

        # Create grounds relation: source → observation
        graph.add_relation(source_id, obs.id, "grounds")
        created += 1


@behavior(
    name="task_linker",
    on=["object.created"],
    where={"object.type": "observation"},
    creates=[],
)
def task_linker(event, graph, ctx, *, settings: CoreSettings):
    """Link a newly created observation to existing open tasks.

    Computes Jaccard word-overlap between the observation text and each
    open task's title. If overlap >= threshold, creates a 'produces'
    relation from the observation to the task.

    On: object.created (observation)
    Creates: produces(observation → task) [relations only, no new objects]
    """
    obj = event.payload.get("object", {})
    obs_id = obj.get("id")
    obs_data = obj.get("data", {})
    obs_text = obs_data.get("text", "")

    if not obs_text or not obs_id:
        return

    # Find all open tasks in the graph
    open_tasks = [
        o for o in ctx.view.objects(type="task")
        if o.data.get("status") in ("candidate", "active")
    ]

    threshold = settings.task_link_similarity_threshold

    for task in open_tasks:
        task_title = task.data.get("title", "")
        if not task_title:
            continue

        ratio = _overlap_ratio(obs_text, task_title)
        if ratio >= threshold:
            try:
                graph.add_relation(obs_id, task.id, "produces")
            except Exception:
                pass  # Relation may already exist


@behavior(
    name="memory_candidate_proposer",
    on=["object.created"],
    where={"object.type": "observation"},
    creates=["memory_candidate"],
)
def memory_candidate_proposer(event, graph, ctx, *, settings: CoreSettings):
    """Propose a memory_candidate for durable high-confidence observations.

    Only creates candidates for observations categorized as preference,
    decision, fact, or instruction with confidence >= 0.7.
    Memory Gateway Pack decides whether to actually store them.

    On: object.created (observation, category in {preference, decision, fact, instruction})
    Creates: memory_candidate, proposes(observation → memory_candidate)
    """
    obj = event.payload.get("object", {})
    obs_id = obj.get("id")
    obs_data = obj.get("data", {})

    category = obs_data.get("category")
    confidence = obs_data.get("confidence", 0.0)
    text = obs_data.get("text", "")
    source_ids = obs_data.get("source_ids", [])
    frame_id = obs_data.get("frame_id")

    # Only propose memory for durable, high-confidence observations
    if category not in ("preference", "decision", "fact", "instruction"):
        return
    if confidence < 0.7:
        return
    if not text:
        return

    candidate = graph.add_object(
        "memory_candidate",
        MemoryCandidate(
            text=text,
            confidence=confidence,
            source_ids=source_ids,
            observation_ids=[obs_id],
            category=category,
            accepted=settings.auto_accept_memory_candidates,
            frame_id=frame_id,
        ).model_dump(),
    )

    graph.add_relation(obs_id, candidate.id, "proposes")


BEHAVIORS = [observation_extractor, task_linker, memory_candidate_proposer]
