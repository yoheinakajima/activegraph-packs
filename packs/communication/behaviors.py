"""Communication Pack behaviors — v0.1.

Behaviors:
  intent_detector      — comm_message.created → CommIntent (heuristic classification)
  thread_tracker       — comm_message.created → creates/updates CommThread + CommParticipant
  response_dispatcher  — comm_response_candidate.created (status=approved) → marks sent

All behaviors are deterministic in v0.1 (heuristic intent detection, no LLM required).

Registry:
  _THREAD_REGISTRY maps "channel::thread_key" → comm_thread_id
  _THREAD_MESSAGE_COUNT maps comm_thread_id → message_count
  Call clear_thread_registry() between test fixtures.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from activegraph.packs import behavior

from .settings import CommunicationSettings

# ================================================================ Thread registry

_THREAD_REGISTRY: dict[str, str] = {}
_THREAD_MESSAGE_COUNT: dict[str, int] = {}


def clear_thread_registry() -> None:
    """Reset thread registry — call between test fixtures."""
    _THREAD_REGISTRY.clear()
    _THREAD_MESSAGE_COUNT.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _thread_key(channel: str, thread_id_hint: Optional[str]) -> str:
    return f"{channel}::{thread_id_hint or '__root__'}"


# ================================================================ Intent patterns

_INTENT_PATTERNS: dict[str, list[str]] = {
    "query": [
        "?", "what is", "what are", "how do", "how does", "how can", "why is",
        "who is", "where is", "when is", "tell me", "explain", "can you tell",
        "what's", "how's", "do you know", "could you explain",
    ],
    "request": [
        "please", "can you", "could you", "would you", "draft", "write",
        "create", "generate", "make", "build", "send", "schedule", "set up",
        "prepare", "summarize", "review and", "help me", "i need",
        "we need", "i'd like", "i would like",
    ],
    "reply": [
        "in reply to", "as discussed", "following up", "per our", "as mentioned",
        "as i said", "re:", "thanks for", "thank you for", "appreciated",
        "got it", "understood", "noted", "sounds good", "agreed",
    ],
    "notification": [
        "fyi", "for your information", "just to let you know", "heads up",
        "update:", "status update", "letting you know", "wanted to inform",
        "just wanted to share", "quick note",
    ],
    "approval_request": [
        "approve", "approval", "permission", "authorize", "lgtm",
        "sign off", "sign-off", "okay to proceed", "can i proceed",
        "is it okay", "do i have permission", "requesting approval",
        "need your approval", "requires approval", "pending approval",
        "waiting for approval", "please approve", "require your sign",
        "your authorization", "your authorisation",
    ],
    "review": [
        "review", "take a look", "feedback", "thoughts on", "what do you think",
        "check this", "look over", "look at", "opinion on", "evaluate",
        "assess", "critique",
    ],
}


# Priority for tie-breaking: more specific intents win over generic ones.
_INTENT_PRIORITY: dict[str, int] = {
    "approval_request": 6,
    "review": 5,
    "notification": 4,
    "reply": 3,
    "request": 2,
    "query": 1,
    "unknown": 0,
}


def _classify_intent(content: str, intent_hint: Optional[str]) -> tuple[str, float, str]:
    """Classify intent using keyword heuristics. Returns (intent, confidence, reasoning).

    Tie-breaking: more specific intents (approval_request, review, notification)
    win over generic ones (request, query) when hit counts are equal.
    """
    if intent_hint and intent_hint in _INTENT_PATTERNS:
        return intent_hint, 0.9, "provided via intent_hint"

    content_lower = content.lower()
    scores: dict[str, int] = {}

    for intent, patterns in _INTENT_PATTERNS.items():
        hits = sum(1 for p in patterns if p in content_lower)
        if hits > 0:
            scores[intent] = hits

    if not scores:
        return "unknown", 0.4, "no intent signals found in content"

    # Sort by (hit_count DESC, priority DESC) so specific intents win ties
    best = max(scores, key=lambda k: (scores[k], _INTENT_PRIORITY.get(k, 0)))
    confidence = min(0.55 + (scores[best] * 0.12), 0.93)

    runners = sorted(
        scores.items(),
        key=lambda x: (x[1], _INTENT_PRIORITY.get(x[0], 0)),
        reverse=True,
    )
    secondary = runners[1][0] if len(runners) >= 2 else None

    reasoning = f"matched {scores[best]} signal(s) for '{best}'"
    if secondary:
        reasoning += f"; secondary: '{secondary}'"

    return best, confidence, reasoning


# ================================================================ Behaviors


@behavior(
    name="intent_detector",
    on=["object.created"],
    where={"object.type": "comm_message"},
    creates=["comm_intent"],
)
def intent_detector(event, graph, ctx, *, settings: CommunicationSettings):
    """Classify inbound CommMessage intent using keyword/pattern heuristics.

    On: object.created (comm_message)
    Creates: comm_intent + intent_of relation
    Fires only on inbound messages (direction='inbound').
    """
    obj = event.payload.get("object", {})
    msg_id = obj.get("id")
    msg_data = obj.get("data", {})

    if msg_data.get("direction") != "inbound":
        return

    content = msg_data.get("content") or ""
    intent_hint = msg_data.get("intent_hint")
    threshold = settings.low_confidence_intent_threshold

    intent, confidence, reasoning = _classify_intent(content, intent_hint)
    if confidence < threshold:
        intent = "unknown"
        reasoning = f"confidence {confidence:.2f} below threshold {threshold}"

    try:
        comm_intent = graph.add_object("comm_intent", {
            "message_id": msg_id,
            "thread_id": msg_data.get("thread_id"),
            "intent": intent,
            "confidence": confidence,
            "reasoning": reasoning,
            "detected_by": "intent_detector",
            "frame_id": msg_data.get("frame_id"),
        })
        graph.add_relation(comm_intent.id, msg_id, "intent_of")
    except Exception:
        pass


@behavior(
    name="intent_router",
    on=["object.created"],
    where={"object.type": "comm_intent"},
    creates=["capability_call"],
)
def intent_router(event, graph, ctx, *, settings: CommunicationSettings):
    """Route actionable intents into the Tool Gateway as capability proposals.

    On: object.created (comm_intent)
    Creates: capability_call (status=proposed) + fulfills_intent relation

    The deterministic half of "chat that can act": when an intent kind has a
    configured route (settings.intent_routes) and the detection confidence
    clears intent_route_min_confidence, this PROPOSES a capability call —
    nothing more. The normal gateway lifecycle (policy_enforcer →
    approval/hold → call_executor → result_sourcer) governs whether and how
    it runs, so intent-triggered actions carry exactly the same audit and
    approval weight as any other capability call.

    Degrades gracefully: with no routes configured (the default) or without
    the Tool Gateway Pack loaded (capability_call type absent → add_object
    fails, swallowed) this is a no-op and intents stay informational.
    """
    routes = settings.intent_routes
    if not routes:
        return

    obj = event.payload.get("object", {})
    intent_id = obj.get("id")
    data = obj.get("data", {})
    intent_kind = data.get("intent")
    route = routes.get(intent_kind)
    if not route:
        return
    if float(data.get("confidence") or 0.0) < settings.intent_route_min_confidence:
        return

    # The intent records message_id; the message carries the text the
    # capability receives. get_object(id) is the behavior-safe read.
    message_id = data.get("message_id")
    try:
        msg = graph.get_object(message_id) if message_id else None
    except Exception:
        msg = None
    content = (msg.data.get("content") if msg else "") or ""

    content_field = route.get("content_field", "text")
    input_data = {**route.get("input", {}), content_field: content}

    try:
        call = graph.add_object("capability_call", {
            "provider_id": route.get("provider_id", ""),
            "provider_name": route.get("provider_name", ""),
            "capability_name": route.get("capability_name", ""),
            "input_data": input_data,
            "credential_ref_name": route.get("credential_ref_name"),
            "risk_class": route.get("risk_class", "medium"),
            "status": "proposed",
            "proposed_by": "intent_router",
            "frame_id": data.get("frame_id"),
            "proposed_at": _now_iso(),
            "metadata": {"intent_id": intent_id, "intent": intent_kind},
        })
        # NOTE: add_relation signature is (source, target, type).
        graph.add_relation(call.id, intent_id, "fulfills_intent")
    except Exception:
        pass  # Tool Gateway not loaded — intents stay informational.


@behavior(
    name="thread_tracker",
    on=["object.created"],
    where={"object.type": "comm_message"},
    creates=["comm_thread", "comm_participant"],
)
def thread_tracker(event, graph, ctx, *, settings: CommunicationSettings):
    """Create or update CommThread for incoming messages.

    On: object.created (comm_message)
    Creates/updates: comm_thread, comm_participant
    Creates: thread_contains relation
    Patches: comm_message.thread_id

    Uses _THREAD_REGISTRY keyed by (channel, thread_id_hint) — no graph.objects() scan.
    """
    obj = event.payload.get("object", {})
    msg_id = obj.get("id")
    msg_data = obj.get("data", {})

    channel = msg_data.get("channel") or settings.default_channel
    now = _now_iso()
    meta = msg_data.get("metadata") or {}
    thread_id_hint = (
        msg_data.get("thread_id")
        or meta.get("thread_id_hint")
    )
    key = _thread_key(channel, thread_id_hint)
    sender_ref = msg_data.get("sender_ref") or ""

    if key in _THREAD_REGISTRY:
        thread_id = _THREAD_REGISTRY[key]
        _THREAD_MESSAGE_COUNT[thread_id] = _THREAD_MESSAGE_COUNT.get(thread_id, 0) + 1
        try:
            graph.patch_object(thread_id, {
                "last_message_at": now,
                "message_count": _THREAD_MESSAGE_COUNT[thread_id],
            })
        except Exception:
            pass
        if not msg_data.get("thread_id"):
            try:
                graph.patch_object(msg_id, {"thread_id": thread_id})
            except Exception:
                pass

    elif settings.auto_create_threads:
        try:
            thread = graph.add_object("comm_thread", {
                "channel": channel,
                "subject": meta.get("subject"),
                "participant_ids": [],
                "status": "open",
                "created_at": now,
                "last_message_at": now,
                "message_count": 1,
                "frame_id": msg_data.get("frame_id"),
            })
            thread_id = thread.id
            _THREAD_REGISTRY[key] = thread_id
            _THREAD_MESSAGE_COUNT[thread_id] = 1
            try:
                graph.patch_object(msg_id, {"thread_id": thread_id})
            except Exception:
                pass
        except Exception:
            return

    else:
        return

    try:
        graph.add_relation(thread_id, msg_id, "thread_contains")
    except Exception:
        pass

    if sender_ref:
        try:
            role = "sender" if msg_data.get("direction") == "inbound" else "recipient"
            graph.add_object("comm_participant", {
                "thread_id": thread_id,
                "principal_ref": sender_ref,
                "role": role,
                "joined_at": now,
                "frame_id": msg_data.get("frame_id"),
            })
        except Exception:
            pass


@behavior(
    name="response_dispatcher",
    on=["object.created"],
    where={"object.type": "comm_response_candidate"},
)
def response_dispatcher(event, graph, ctx, *, settings: CommunicationSettings):
    """Dispatch approved CommResponseCandidates to channel adapters.

    On: object.created (comm_response_candidate, status='approved')
    Creates: dispatched_to relation (candidate → thread)
    Patches: comm_response_candidate.status = 'sent'

    Channel pack responders handle the actual content delivery;
    this behavior marks the candidate as dispatched and creates the audit trail.
    """
    if not settings.auto_dispatch_approved_responses:
        return

    obj = event.payload.get("object", {})
    candidate_id = obj.get("id")
    data = obj.get("data", {})

    if data.get("status") != "approved":
        return

    thread_id = data.get("thread_id")
    if thread_id:
        try:
            graph.add_relation(candidate_id, thread_id, "dispatched_to")
        except Exception:
            pass

    try:
        graph.patch_object(candidate_id, {"status": "sent"})
    except Exception:
        pass


# ================================================================ BEHAVIORS registry

BEHAVIORS = [intent_detector, intent_router, thread_tracker, response_dispatcher]
