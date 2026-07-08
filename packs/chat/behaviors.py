"""Chat Adapter Pack behaviors — v0.2.

Behaviors:
  chat_ingester          — chat_input.created → Source + CommMessage + ChatSession + ChatTurn
  chat_context_assembler — comm_message.created (chat, inbound) → ChatContext (graph-native memory)
  chat_profile_context   — comm_message.created (chat, inbound) → ProfileContextView (self-knowledge)
  chat_llm_responder     — comm_message.created (chat, inbound) → CommResponseCandidate
  chat_responder         — comm_response_candidate.created (chat, approved) → ChatTurn updated

Self-knowledge (who are you? / what is your mission?):
  chat_profile_context reuses the Agent Profile Pack to assemble the assistant's
  identity into a ProfileContextView and links it to the inbound CommMessage via
  provides_context_for — the same seam chat_context_assembler uses for memory. The
  responder's depth-1 view then folds both into the prompt. Agent Profile is an
  optional partner: without it, the behavior no-ops and chat runs identity-free.

Graph-native conversation memory (v0.2):
  Conversation memory is reconstructed from the graph, not from process memory.
  chat_context_assembler reads prior ChatTurns for the session (linked via
  session_contains_turn), formats them into a ChatContext, and links it to the
  inbound CommMessage. chat_llm_responder's depth-1 view then captures that
  ChatContext, so the prior turns reach the LLM. Because the turns are read from
  persisted graph objects, conversation context survives an API-server restart
  mid-session and needs no in-process state. See chat_context_assembler for the
  two approaches considered and why this one was chosen.

In-process maps (convenience caches only — never the source of truth):
  _SESSION_REGISTRY    user_ref → {"session_id", "turn_count"} — resume-by-user shortcut.
  _MESSAGE_TO_TURN     comm_message_id → chat_turn_id — delivery plumbing for chat_responder.
  _MESSAGE_TO_SESSION  comm_message_id → session_id — delivery plumbing.
  Every value these hold is also derivable from the graph; the behaviors fall
  back to graph lookups when a cache misses (e.g. after a restart). They exist
  purely to avoid graph scans on the hot path. Call clear_session_registry()
  between test fixtures.

Thread continuity:
  CommMessage.metadata.thread_id_hint is set to session_id so each session gets its
  own distinct comm_thread (keyed "chat::<session_id>" by thread_tracker).

LLM wiring (native):
  chat_llm_responder is an @llm_behavior — the runtime owns the LLM lifecycle
  (emits llm.requested → provider.complete() → llm.responded) and assembles the
  prompt from the triggering comm_message plus a scoped graph view. The active
  provider is set on the Runtime (see packs/chat/llm.py: select_chat_provider).
  With no provider key configured the MockChatProvider serves an instructive
  canned reply, so the full pipeline runs end-to-end without an API key.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from activegraph.packs import behavior, llm_behavior

from .llm import ChatReply
from .settings import ChatSettings

# ================================================================ Session caches
# These are convenience caches, NOT the source of truth. Conversation memory and
# session identity live in the graph; these maps just avoid graph scans on the
# hot path and are rebuilt opportunistically. Behaviors fall back to the graph
# when a cache misses (e.g. after a restart wipes them).

_SESSION_REGISTRY: dict[str, dict] = {}
_MESSAGE_TO_TURN: dict[str, str] = {}
_MESSAGE_TO_SESSION: dict[str, str] = {}


def clear_session_registry() -> None:
    """Reset the in-process caches — call between test fixtures (and a useful
    way to simulate an API-server restart, since the graph is the real store)."""
    _SESSION_REGISTRY.clear()
    _MESSAGE_TO_TURN.clear()
    _MESSAGE_TO_SESSION.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ================================================================ Behaviors


@behavior(
    name="chat_ingester",
    on=["object.created"],
    where={"object.type": "chat_input"},
    creates=["source", "comm_message", "chat_session", "chat_turn"],
)
def chat_ingester(event, graph, ctx, *, settings: ChatSettings):
    """Translate ChatInput into CommMessage + ChatSession + ChatTurn.

    On: object.created (chat_input)
    Creates: source(kind=chat_message), comm_message(channel=chat, inbound),
             chat_session (or resumes), chat_turn
    Relations: derived_from_source, session_contains_turn, turn_from_input

    Session continuity: resumes existing session by user_ref (or explicit session_id).
    Thread continuity: uses session_id as thread_id_hint so thread_tracker creates a
      per-session comm_thread (key="chat::<session_id>") rather than a shared root.
    CommThread is created downstream by thread_tracker (Communication Pack).
    """
    obj = event.payload.get("object", {})
    input_id = obj.get("id")
    inp = obj.get("data", {})

    user_ref = inp.get("user_ref") or "unknown_user"
    content = inp.get("content") or ""
    given_session_id = inp.get("session_id")
    frame_id = inp.get("frame_id")
    now = _now_iso()

    # ── Channel resolution ────────────────────────────────────────────────
    # chat_input is the entry point for EVERY interactive conversation, not
    # just the web console: messenger adapters (telegram, whatsapp, ...) emit
    # chat_input with metadata.channel set, reusing this pack's session,
    # memory, gating, and responder machinery wholesale. The channel rides on
    # the comm_message so outbound delivery routes correctly; adapter routing
    # data (chat ids, phone numbers) travels under metadata.adapter.
    input_meta = dict(inp.get("metadata") or {})
    channel = input_meta.pop("channel", None) or "chat"

    # ── 1. Resolve or create ChatSession ──────────────────────────────────
    # Resolution order, graph-first so it is restart-safe:
    #   1. Explicit session_id → resume directly from the persisted ChatSession
    #      object (graph.get_object). This is what the Inspector UI sends, and
    #      it works even after a restart has wiped the in-process caches.
    #   2. No session_id → resume the user's most recent session via the
    #      _SESSION_REGISTRY cache (a convenience; falls through to a new
    #      session on a cache miss).
    #   3. Otherwise → create a new session.
    session_key = user_ref
    session_id: Optional[str] = None
    turn_count = 1

    if given_session_id:
        try:
            sess = graph.get_object(given_session_id)
        except Exception:
            sess = None
        if sess is not None:
            session_id = given_session_id
            turn_count = int(sess.data.get("turn_count", 0)) + 1

    if session_id is None and session_key in _SESSION_REGISTRY:
        reg = _SESSION_REGISTRY[session_key]
        session_id = reg["session_id"]
        turn_count = reg["turn_count"] + 1

    if session_id is None:
        try:
            session_obj = graph.add_object("chat_session", {
                "user_ref": user_ref,
                "started_at": now,
                "status": "active",
                "turn_count": 0,
                "llm_config": {},
                "frame_id": frame_id,
            })
            session_id = session_obj.id
        except Exception:
            return
        turn_count = 1

    # thread_id_hint = session_id ensures each session maps to its own
    # comm_thread (keyed "chat::<session_id>" by thread_tracker).
    thread_id_hint = session_id

    # ── Reply gate: identity on the respond path ──────────────────────────
    # Decided HERE (at ingestion) and stamped onto the comm_message, because
    # chat_llm_responder matches the verdict declaratively in its `where`
    # clause — that is what lets a gated sender skip the (paid) LLM call
    # entirely. Fail-closed under restrictive policies; blocked senders are
    # deflected even under "open". See packs/communication/gating.py.
    try:
        from packs.communication.gating import decide_reply

        gate = decide_reply(graph, user_ref, reply_policy=settings.reply_policy)
    except Exception:
        # Communication pack is a hard requirement of chat, so this only
        # trips on truly broken installs — behave like the open default.
        gate = {"gate": "open", "role": None, "reason": "gating unavailable"}

    # ── 2. Create Core Source ──────────────────────────────────────────────
    try:
        source = graph.add_object("source", {
            "kind": "chat_message",
            "content": content,
            "channel": channel,
            "sender_ref": user_ref,
            "frame_id": frame_id,
            "metadata": {
                "session_id": session_id,
                "turn_number": turn_count,
            },
        })
        source_id = source.id
    except Exception:
        return

    # ── 3. Create CommMessage ──────────────────────────────────────────────
    try:
        comm_msg = graph.add_object("comm_message", {
            "channel": channel,
            "sender_ref": user_ref,
            "content": content,
            "direction": "inbound",
            "source_id": source_id,
            "thread_id": None,
            "frame_id": frame_id,
            "metadata": {
                "thread_id_hint": thread_id_hint,
                "session_id": session_id,
                # The adapter's declaration that this message wants an
                # interactive reply — what the responders' `where` matches
                # (channel-neutral; the channel field routes delivery).
                "conversational": True,
                "reply_gate": gate["gate"],
                "reply_gate_reason": gate["reason"],
                "sender_role": gate["role"],
                "adapter": input_meta,
            },
        })
        comm_msg_id = comm_msg.id
    except Exception:
        return

    try:
        graph.add_relation(comm_msg_id, source_id, "derived_from_source")
    except Exception:
        pass

    # ── 4. Create ChatTurn ─────────────────────────────────────────────────
    try:
        turn = graph.add_object("chat_turn", {
            "session_id": session_id,
            "user_message": content,
            "assistant_message": None,
            "turn_number": turn_count,
            "comm_message_id": comm_msg_id,
            "frame_id": frame_id,
        })
        turn_id = turn.id
    except Exception:
        return

    try:
        graph.add_relation(session_id, turn_id, "session_contains_turn")
    except Exception:
        pass
    try:
        graph.add_relation(turn_id, input_id, "turn_from_input")
    except Exception:
        pass

    # ── 5. Update session turn count + registries ──────────────────────────
    try:
        graph.patch_object(session_id, {"turn_count": turn_count})
    except Exception:
        pass

    # Refresh the convenience caches (hot-path shortcuts, not the source of
    # truth — every value here is also recorded on the graph objects above).
    _MESSAGE_TO_TURN[comm_msg_id] = turn_id
    _MESSAGE_TO_SESSION[comm_msg_id] = session_id
    _SESSION_REGISTRY[session_key] = {
        "session_id": session_id,
        "turn_count": turn_count,
    }
    # NOTE (v0.2): no in-process turn history is recorded here. Prior turns are
    # read back from the graph by chat_context_assembler, which is what makes
    # conversation memory restart-safe.


@behavior(
    name="chat_context_assembler",
    on=["object.created"],
    where={
        "object.type": "comm_message",
        "object.data.channel": "chat",
        "object.data.direction": "inbound",
    },
    # ── How prior turns reach the LLM ──────────────────────────────────────
    # CONTRACT: the runtime builds every LLM prompt from the serialized graph
    # *view* — a behavior never hand-writes prompt text. So for the model to
    # "remember" earlier turns, those turns must appear in the responder's view.
    # Two ActiveGraph-idiomatic ways to achieve that:
    #
    #   (a) Widen chat_llm_responder's own view to reach the ChatSession and
    #       its turns. Simplest possible change, but: the turns arrive as raw,
    #       scattered chat_turn objects; the responder cannot bound them to
    #       max_context_messages (the view pulls *every* turn in the session);
    #       and "what context did we actually send?" is never recorded.
    #
    #   (b) THIS behavior: assemble one ChatContext object that the responder
    #       reads. Mirrors agent_profile's profile_context_provider
    #       (request → context view). The responder stays declarative — its
    #       existing depth-1 view captures the linked ChatContext for free — the
    #       context is bounded and pre-formatted, and the exact memory handed to
    #       the model becomes a first-class, inspectable graph object.
    #
    # We choose (b): a declarative responder plus auditable, bounded memory.
    #
    # The view below anchors at the ChatSession (resolved from the inbound
    # message's metadata.session_id) at depth 1, so it contains the session plus
    # every chat_turn linked by session_contains_turn. Prior turns are therefore
    # read from the *graph*, never an in-process dict — which is exactly why
    # conversation memory survives an API-server restart mid-session.
    view={
        "around": "event.payload.object.data.metadata.session_id",
        "depth": 1,
        "recent_events": 0,
    },
    creates=["chat_context"],
)
def chat_context_assembler(event, graph, ctx, *, settings: ChatSettings):
    """Assemble graph-native conversation memory for an inbound chat message.

    On: object.created (comm_message, channel=chat, direction=inbound)
    Creates: chat_context (the prior conversation, bounded + formatted)
    Relations: provides_context_for (chat_context → comm_message)

    Reads prior ChatTurns for the session from the session-anchored graph view,
    formats the most recent ``max_context_messages`` of them into a transcript,
    and links the result to the inbound message so chat_llm_responder's depth-1
    view picks it up. Skips silently on the first turn (no prior memory yet).
    """
    obj = event.payload.get("object", {})
    msg_id = obj.get("id")
    msg_data = obj.get("data", {})
    meta = msg_data.get("metadata") or {}
    if meta.get("reply_gate") == "deflect":
        return  # Gated sender: template reply only — no context assembly.
    session_id = meta.get("session_id")
    frame_id = msg_data.get("frame_id")
    if not session_id:
        return

    # Prior turns come from the graph view (restart-safe), not a process dict.
    turns = [
        o for o in ctx.view.objects()
        if o.type == "chat_turn" and o.data.get("session_id") == session_id
    ]
    # Exclude the current, still-unanswered turn — it is the triggering event.
    prior = [t for t in turns if t.data.get("comm_message_id") != msg_id]
    if not prior:
        return  # First turn of the session: nothing to remember yet.

    prior.sort(key=lambda t: t.data.get("turn_number", 0))
    # Bound memory to the most recent N turns (0/negative → unbounded).
    limit = settings.max_context_messages
    if limit and limit > 0:
        prior = prior[-limit:]
    if not prior:
        return

    lines = [f"Conversation so far (most recent {len(prior)} turn(s)):"]
    for t in prior:
        n = t.data.get("turn_number", "?")
        user = (t.data.get("user_message") or "").strip()
        assistant = (t.data.get("assistant_message") or "").strip()
        lines.append(f"[{n}] User: {user}")
        if assistant:
            lines.append(f"    Assistant: {assistant}")
    transcript = "\n".join(lines)

    try:
        context = graph.add_object("chat_context", {
            "session_id": session_id,
            "message_id": msg_id,
            "turn_count": len(prior),
            "transcript": transcript,
            "frame_id": frame_id,
        })
        # Link to the inbound message so the responder's depth-1 view captures
        # this context without having to widen its own scope.
        # NOTE: add_relation signature is (source, target, type).
        graph.add_relation(context.id, msg_id, "provides_context_for")
    except Exception:
        pass


@behavior(
    name="chat_profile_context",
    on=["object.created"],
    where={
        "object.type": "comm_message",
        "object.data.channel": "chat",
        "object.data.direction": "inbound",
    },
    creates=["profile_context_view"],
)
def chat_profile_context(event, graph, ctx, *, settings: ChatSettings):
    """Fold the assistant's self-knowledge into the LLM context for a chat turn.

    On: object.created (comm_message, channel=chat, direction=inbound)
    Creates: profile_context_view (the assistant's identity/mission/etc.)
    Relations: provides_context_for (profile_context_view → comm_message)

    This is what lets the assistant answer "who are you?" / "what is your
    mission?" — the AgentProfile is assembled into a ProfileContextView, linked
    to the inbound message, and chat_llm_responder's depth-1 view then serializes
    it into the prompt (the runtime assembles every prompt from the view; a
    behavior never hand-writes prompt text). It mirrors chat_context_assembler:
    same seam (provides_context_for → comm_message), different payload (identity
    vs. conversation memory).

    ── Why assemble inline rather than reuse the request→view cascade? ──────────
    Agent Profile's idiomatic path is profile_context_request → (provider) →
    profile_context_view. We deliberately do NOT use it here: the request would
    be processed in a *later* event cascade, so the resulting view would not
    exist yet when chat_llm_responder fires in this same batch — the context
    would arrive one turn too late. Instead we call agent_profile's
    assemble_profile_view() synchronously and create the view now, so the edge is
    in place before the responder's view is built. We still reuse the pack's
    assembly *logic* (one shared builder), just not its asynchronous plumbing.

    ── Decoupling ──────────────────────────────────────────────────────────────
    Agent Profile is an OPTIONAL composition partner. The import is guarded, so
    if the Chat Pack is loaded without agent_profile this behavior simply does
    nothing and chat runs identity-free.

    ── system_prompt_override escape hatch ─────────────────────────────────────
    The framework system prompt is the responder's static @llm_behavior
    description, which can't be swapped per-call. So when an operator sets
    system_prompt_override, we honor it through the same view seam: inject the
    literal string as the sole context (and skip the profile entirely). It is a
    bypass — the profile does not contribute when an override is set.

    ── Precedence (two independent knobs) ──────────────────────────────────────
    `system_prompt_override` and `include_profile` are checked independently, and
    the override is the more specific, more intentional setting, so it wins:
      1. system_prompt_override set  → inject the override (even if
         include_profile=False — an explicit override is always honored).
      2. else include_profile=True   → assemble the AgentProfile view.
      3. else                        → inject nothing (responder falls back to
         its static system prompt).
    """
    obj = event.payload.get("object", {})
    msg_id = obj.get("id")
    msg_data = obj.get("data", {})
    frame_id = msg_data.get("frame_id")
    if not msg_id:
        return
    if (msg_data.get("metadata") or {}).get("reply_gate") == "deflect":
        return  # Gated sender: template reply only — no profile assembly.

    # ── Escape hatch: literal system-prompt override (bypasses the profile) ──
    # Checked BEFORE the include_profile gate: an explicitly configured override
    # is an intentional, specific instruction and is always honored.
    override = settings.system_prompt_override
    if override is not None:
        try:
            view = graph.add_object("profile_context_view", {
                "profile_id": "",
                "channel": "chat",
                "audience_role": "owner",
                "agent_name": "",
                "mission": "",
                "standing_instructions": [override],
                "frame_id": frame_id,
                "metadata": {"origin": "system_prompt_override"},
            })
            graph.add_relation(view.id, msg_id, "provides_context_for")
        except Exception:
            pass
        return

    # No override → the profile path is gated by include_profile.
    if not settings.include_profile:
        return

    # ── Reuse Agent Profile's assembly (guarded — optional dependency) ───────
    try:
        from packs.agent_profile.behaviors import assemble_profile_view
        from packs.agent_profile.settings import AgentProfileSettings
    except Exception:
        return  # agent_profile not installed/loaded → run identity-free.

    # ── Audience-aware assembly ──────────────────────────────────────────────
    # The profile view is shaped by WHO is asking: agent_profile suppresses the
    # mission and filters instructions/goals for external audiences. The
    # sender's role was resolved at ingestion (identity registry) and stamped
    # on the message by chat_ingester; unresolved senders assemble as
    # "unknown" — the external-shaped view — never as owner. (Chat used to
    # hardcode audience_role="owner" for every requester, which handed
    # strangers the owner-framed identity.)
    audience_role = (msg_data.get("metadata") or {}).get("sender_role") or "unknown"
    view_model = assemble_profile_view(
        graph,
        settings=AgentProfileSettings(),
        channel="chat",
        audience_role=audience_role,
        frame_id=frame_id,
    )
    if view_model is None:
        return  # No profile registered yet — nothing to inject.

    try:
        view = graph.add_object("profile_context_view", view_model.model_dump())
        # Same edge chat_context uses, so the responder's depth-1 view captures
        # it for free. NOTE: add_relation signature is (source, target, type).
        graph.add_relation(view.id, msg_id, "provides_context_for")
    except Exception:
        pass


# ================================================================ Long-term memory
#
# Two behaviors connect chat to the Memory Gateway lifecycle so the assistant
# both BUILDS durable memory and USES it across sessions:
#
#   chat_memory_proposer  (WRITE)  — turns durable statements in a chat message
#                                     into memory_candidate objects.
#   chat_memory_context   (READ)   — recalls relevant memories for the inbound
#                                     message and folds them into the prompt.
#
# Both are unopinionated and pluggable (see ChatSettings.memory_* and
# docs/long-term-memory.md).


# Conversation-tuned cues for the default heuristic write path. Each maps a
# small set of first-person/durable phrasings to a memory category. This is
# deliberately tiny and explainable — it is NOT meant to be exhaustive NLP. The
# point is a zero-LLM, zero-cost baseline that captures the obvious cases; richer
# extraction is a swap-in (set memory_write_path="off" and load an ingestion
# pack that emits memory_candidate objects — that object IS the seam).
_CHAT_MEMORY_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("preference", (
        "i prefer", "i'd prefer", "i would prefer", "i like", "i love",
        "i hate", "i dislike", "i enjoy", "my favorite", "my favourite",
        "i'd rather", "i would rather",
    )),
    ("instruction", (
        "always", "never", "please make sure", "make sure", "remember to",
        "remember that", "from now on", "call me", "don't ", "do not ",
    )),
    ("decision", (
        "we decided", "i decided", "let's go with", "we'll use", "i'll use",
        "we are going with", "i'm going with", "going forward we",
    )),
    # First-person durable facts Core's generic keyword extractor tends to miss
    # ("i'm vegetarian", "my name is …"). Kept last so the more specific
    # preference/instruction/decision cues win.
    ("fact", (
        "my name is", "i am ", "i'm ", "my email", "my timezone",
        "i live in", "i work", "i'm based in",
    )),
)


_INTERROGATIVE_STARTS = (
    "what", "who", "whom", "whose", "where", "when", "why", "how", "which",
    "can ", "could ", "do ", "does ", "did ", "is ", "are ", "was ", "were ",
    "will ", "would ", "should ", "shall ", "have ", "has ",
)


def _is_question(text: str) -> bool:
    """A question is not a memory candidate. Catches both punctuated
    ("What's my favorite color?") and bare ("what's my favorite color")
    interrogatives — the cue matcher would otherwise see "my favorite" and
    memorize the QUESTION as a preference."""
    stripped = text.strip().lower()
    return stripped.endswith("?") or stripped.startswith(_INTERROGATIVE_STARTS)


def _classify_chat_memory(text: str) -> Optional[str]:
    """Return a memory category for *text* if it states something durable, else
    None. Small, ordered keyword heuristic — see _CHAT_MEMORY_CUES. Questions
    never classify (see _is_question)."""
    if _is_question(text):
        return None
    low = text.lower()
    for category, cues in _CHAT_MEMORY_CUES:
        if any(cue in low for cue in cues):
            return category
    return None


@behavior(
    name="chat_memory_proposer",
    on=["object.created"],
    where={
        "object.type": "comm_message",
        "object.data.channel": "chat",
        "object.data.direction": "inbound",
    },
    creates=["memory_candidate"],
)
def chat_memory_proposer(event, graph, ctx, *, settings: ChatSettings):
    """Default heuristic WRITE path: chat turn → memory_candidate.

    On: object.created (comm_message, channel=chat, direction=inbound)
    Creates: memory_candidate (when the message states a durable preference/
             instruction/decision/fact)

    ── The seam (how to swap this out) ─────────────────────────────────────────
    The contract between "noticing something worth remembering" and "the memory
    lifecycle" is the memory_candidate object. Memory Gateway's candidate_evaluator
    → memory_writer pick up ANY memory_candidate, no matter who created it. So an
    alternative ingestion strategy — an LLM extractor, an entity-extraction pack,
    a mem0 importer — replaces this behavior simply by emitting memory_candidate
    objects of its own; set ChatSettings.memory_write_path="off" to silence this
    default and avoid double-proposing. No edits to the Chat Pack are required.

    ── Why a heuristic by default ──────────────────────────────────────────────
    It is zero-cost and zero-dependency: the assistant builds memory with no LLM
    calls and no API key. Core already proposes candidates from the message's
    source via generic extraction; this conversation-tuned pass adds first-person
    durable cues Core misses ("call me Alex", "i'm vegetarian"). Duplicates
    between the two paths are collapsed downstream by memory_writer's text dedup,
    so running both is safe.
    """
    if settings.memory_write_path == "off":
        return  # An external ingestion pack owns the write path.
    if settings.memory_write_path != "heuristic":
        return  # Unknown mode → do nothing rather than guess.

    obj = event.payload.get("object", {})
    msg_id = obj.get("id")
    data = obj.get("data", {})
    if (data.get("metadata") or {}).get("reply_gate") == "deflect":
        return  # Don't memorize senders the assistant won't converse with.
    content = (data.get("content") or "").strip()
    frame_id = data.get("frame_id")
    if not msg_id or len(content) < 6:
        return

    category = _classify_chat_memory(content)
    if category is None:
        return  # Nothing durable detected — most chatter is not memory-worthy.

    source_id = data.get("source_id")
    try:
        graph.add_object("memory_candidate", {
            "text": content,
            # Confident enough to clear the default acceptance_threshold (0.6);
            # governance still decides via the Memory Gateway thresholds.
            "confidence": 0.8,
            "source_ids": [source_id] if source_id else [],
            "observation_ids": [],
            "category": category,
            "subject_ref": data.get("sender_ref"),
            "frame_id": frame_id,
        })
    except Exception:
        pass


@behavior(
    name="chat_memory_context",
    on=["object.created"],
    where={
        "object.type": "comm_message",
        "object.data.channel": "chat",
        "object.data.direction": "inbound",
    },
    creates=["memory_context"],
)
def chat_memory_context(event, graph, ctx, *, settings: ChatSettings):
    """READ path: recall durable memory and fold it into this turn's prompt.

    On: object.created (comm_message, channel=chat, direction=inbound)
    Creates: memory_context (the recalled memories, as prompt-ready text)
    Relations: provides_context_for (memory_context → comm_message)

    This is the half that makes memory *useful*: it queries the Memory Gateway
    backend for memories relevant to the inbound message and attaches the top
    matches to the message via provides_context_for, so chat_llm_responder's
    depth-1 view serializes them straight into the prompt — the same seam
    chat_context_assembler (conversation memory) and chat_profile_context
    (identity) use.

    ── Why retrieve synchronously here, not via memory_retrieval_request? ───────
    Memory Gateway's idiomatic retrieval is graph-driven: memory_retrieval_request
    → memory_retriever → memory_retrieval. But that cascade lands in a LATER event
    batch, so the result would not exist when chat_llm_responder fires in THIS
    batch — the memory would arrive a turn too late. So we call retrieve_memories_fn
    directly and create the context now, mirroring chat_profile_context's choice.
    (Retrieval still updates backend stats, so it remains auditable.)

    ── Backend must match the writer ───────────────────────────────────────────
    We query ChatSettings.memory_backend_url, which MUST equal
    MemoryGatewaySettings.backend_url (both default to ':memory:'). The demo
    server points both at the same SQLite file so recall survives restarts.

    ── Retrieval mode (lexical vs. embeddings) ─────────────────────────────────
    retrieve_memories_fn is mode-agnostic: it is lexical by default and switches
    to embeddings automatically when an embedder is registered on the backend
    (see packs/memory_gateway/backend.py). Nothing here changes either way.
    """
    if not settings.include_memory:
        return  # Cross-session recall disabled.

    obj = event.payload.get("object", {})
    msg_id = obj.get("id")
    data = obj.get("data", {})
    if (data.get("metadata") or {}).get("reply_gate") == "deflect":
        return  # Gated sender: no LLM call happens, so no recall needed.
    query = (data.get("content") or "").strip()
    frame_id = data.get("frame_id")
    if not msg_id or not query:
        return

    # Memory Gateway is an optional partner — guarded import so chat still runs
    # without it (recall simply no-ops).
    try:
        from packs.memory_gateway.tools import retrieve_memories_fn
    except Exception:
        return

    # Access control: by default recall is scoped to the message SENDER, so one
    # user never receives another user's stored memories (a returned memory_context
    # is folded straight into the prompt — unscoped recall would be a cross-user
    # data leak). The sender identity is the same value the write path tags
    # memories with (sender_ref → memory subject_ref). Set
    # ChatSettings.memory_subject_scoped=False to opt into shared/global recall.
    sender_ref = data.get("sender_ref")
    try:
        results = retrieve_memories_fn(
            query=query,
            top_k=settings.memory_top_k,
            min_score=settings.memory_min_score,
            behavior_name="chat_memory_context",
            frame_id=frame_id,
            backend_url=settings.memory_backend_url,
            subject_ref=sender_ref,
            subject_scoped=settings.memory_subject_scoped,
            include_global=settings.memory_include_global,
            # Never recall a memory born in the frame that is asking.
            exclude_frame_id=frame_id,
        )
    except Exception:
        return  # Recall must never break the response.

    if not results:
        return  # Nothing relevant — don't attach an empty context object.

    # Render the recalled memories as plain text. This is what the LLM reads.
    lines = []
    for r in results:
        cat = r.get("category")
        prefix = f"[{cat}] " if cat else ""
        lines.append(f"- {prefix}{r.get('text', '')}")
    summary = "Relevant things you remember about the user:\n" + "\n".join(lines)

    try:
        view = graph.add_object("memory_context", {
            "message_id": msg_id,
            "query": query,
            "item_count": len(results),
            "item_ids": [r.get("item_id") for r in results],
            "summary": summary,
            "frame_id": frame_id,
            "metadata": {},
        })
        # Same edge chat_context uses. NOTE: signature is (source, target, type).
        graph.add_relation(view.id, msg_id, "provides_context_for")
    except Exception:
        pass


_RESPONDER_DESCRIPTION = (
    "You are the assistant in an ActiveGraph-powered chat. Read the user's "
    "most recent message (the triggering comm_message) together with any "
    "prior context in the graph view, then reply helpfully and concisely."
)

_AGENTIC_DESCRIPTION_SUFFIX = (
    " You may call the provided tools to look things up or act on the user's "
    "behalf; ground your reply in their results. If a tool reports "
    "status='held_for_approval', the action was recorded but needs the "
    "owner's sign-off — say so instead of pretending it ran."
)


def make_llm_responder(tools: Optional[list] = None, max_tool_turns: int = 4):
    """Build the chat_llm_responder behavior, optionally with gateway tools.

    The tool-free default (module-level ``chat_llm_responder`` below) is what
    the plain Chat Pack ships. Passing *tools* — Tool objects from
    ``packs.tool_gateway.llm_tools.llm_tools_for`` — produces the AGENTIC
    responder: the runtime's native tool loop drives them, and because each
    is a gateway proxy, every model-initiated action is still recorded,
    policy-checked, credential-injected, and sanitized by the Tool Gateway.

    A factory (not a settings flag) because @llm_behavior binds its tool set
    when the behavior object is constructed; see packs/chat/__init__.py
    build_pack for how ChatSettings.tool_allow_list reaches this.
    """
    return llm_behavior(
        name="chat_llm_responder",
        on=["object.created"],
        # Scoped precisely so the (potentially paid) LLM call fires only for
        # inbound chat messages — never for outbound/email/other comm traffic.
        where={
            "object.type": "comm_message",
            "object.data.direction": "inbound",
            # The adapter's conversational marker (stamped by chat_ingester
            # for every interactive channel — web chat, telegram, whatsapp)
            # replaces channel equality: this responder serves conversations,
            # and the channel field routes the delivery.
            "object.data.metadata.conversational": True,
            # The reply gate stamped by chat_ingester. Gated senders never
            # reach this behavior — and therefore never trigger the LLM call;
            # chat_deflection_responder serves them a bounded template.
            "object.data.metadata.reply_gate": "open",
        },
        description=(
            _RESPONDER_DESCRIPTION + (_AGENTIC_DESCRIPTION_SUFFIX if tools else "")
        ),
        output_schema=ChatReply,
        # model=None → the runtime resolves the model from the active provider's
        # default_model at call time (set via packs/chat/llm.py: select_chat_provider).
        # This also skips cross-family model validation, which a static model name
        # would trip when the configured provider differs.
        model=None,
        view={
            "around": "event.payload.object.id",
            "depth": 1,
            # recent_events MUST stay 0. Prior conversation reaches the model on
            # exactly one path: the ChatContext that chat_context_assembler linked to
            # this message (captured by depth=1 above), whose transcript is already
            # bounded to max_context_messages. A non-zero recent_events would fold
            # raw prior-turn event payloads into the prompt as a second, UNBOUNDED
            # memory channel — defeating max_context_messages and re-introducing a
            # side-channel. Keep memory single-sourced through ChatContext.
            "recent_events": 0,
        },
        creates=["comm_response_candidate"],
        temperature=0.7,
        max_tokens=1024,
        tools=list(tools) if tools else [],
        max_tool_turns=max_tool_turns,
    )(_chat_llm_respond)


def _chat_llm_respond(event, graph, ctx, out, *, settings: ChatSettings):
    """Produce a CommResponseCandidate for inbound chat messages (native LLM).

    On: object.created (comm_message, channel=chat, direction=inbound)
    Creates: comm_response_candidate (status=approved if auto_approve_responses=True)
    Relations: response_to

    The runtime drives the LLM (emitting llm.requested/llm.responded events) and
    hands us ``out`` — a parsed :class:`ChatReply`. We map ``out.reply`` onto a
    CommResponseCandidate; chat_responder then writes it to the ChatTurn.
    """
    obj = event.payload.get("object", {})
    msg_id = obj.get("id")
    msg_data = obj.get("data", {})

    thread_id = msg_data.get("thread_id")
    frame_id = msg_data.get("frame_id")
    msg_meta = msg_data.get("metadata") or {}

    # The prior conversation reaches this LLM call via the ChatContext that
    # chat_context_assembler linked to this message: the depth-1 view below
    # captures it, the runtime serializes it into the prompt, and `out` is the
    # model's reply. "How many prior turns were shown" is recorded on that
    # ChatContext object (chat_context.turn_count) — the auditable, graph-native
    # source of truth — so we don't duplicate it onto the candidate.
    response_content = (getattr(out, "reply", None) or "").strip()
    if not response_content:
        return

    status = "approved" if settings.auto_approve_responses else "proposed"

    try:
        candidate = graph.add_object("comm_response_candidate", {
            "message_id": msg_id,
            "thread_id": thread_id,
            "channel": msg_data.get("channel", "chat"),
            "content": response_content,
            "status": status,
            "created_by_behavior": "chat_llm_responder",
            "frame_id": frame_id,
            # Adapter routing data (chat ids, phone numbers) rides along so a
            # channel dispatcher can deliver without re-reading the message.
            "metadata": {"adapter": msg_meta.get("adapter", {})},
        })
        graph.add_relation(candidate.id, msg_id, "response_to")
    except Exception:
        pass


# The tool-free default responder. Agentic variants are built per-bundle via
# make_llm_responder(tools=...) — see packs/chat/__init__.py build_pack.
chat_llm_responder = make_llm_responder()


@behavior(
    name="chat_deflection_responder",
    on=["object.created"],
    where={
        "object.type": "comm_message",
        "object.data.direction": "inbound",
        "object.data.metadata.conversational": True,
        "object.data.metadata.reply_gate": "deflect",
    },
    creates=["comm_response_candidate"],
)
def chat_deflection_responder(event, graph, ctx, *, settings: ChatSettings):
    """Serve gated senders a bounded template reply — no LLM, no tools.

    On: object.created (comm_message, channel=chat, inbound, reply_gate=deflect)
    Creates: comm_response_candidate (status=approved, metadata.gated=true)
    Relations: response_to

    The mirror of chat_llm_responder for the deflect branch of the reply
    gate: one declarative predicate each, so the split is visible in the
    behavior map rather than buried in an if. Deflection is a decision, not
    silence — the sender gets a polite fixed answer, the LLM is never
    invoked, and the gate verdict is auditable on both the message
    (reply_gate_reason) and the candidate (metadata.gated).
    """
    obj = event.payload.get("object", {})
    msg_id = obj.get("id")
    msg_data = obj.get("data", {})

    try:
        candidate = graph.add_object("comm_response_candidate", {
            "message_id": msg_id,
            "thread_id": msg_data.get("thread_id"),
            "channel": msg_data.get("channel", "chat"),
            "content": settings.deflection_message,
            "status": "approved",
            "created_by_behavior": "chat_deflection_responder",
            "frame_id": msg_data.get("frame_id"),
            "metadata": {
                "gated": True,
                "reply_gate_reason": (msg_data.get("metadata") or {}).get("reply_gate_reason"),
                "adapter": (msg_data.get("metadata") or {}).get("adapter", {}),
            },
        })
        # NOTE: add_relation signature is (source, target, type).
        graph.add_relation(candidate.id, msg_id, "response_to")
    except Exception:
        pass


@behavior(
    name="chat_responder",
    on=["object.created"],
    where={"object.type": "comm_response_candidate"},
)
def chat_responder(event, graph, ctx, *, settings: ChatSettings):
    """Deliver approved chat responses by updating ChatTurn.assistant_message.

    On: object.created (comm_response_candidate, channel=chat, status=approved)
    Patches: chat_turn.assistant_message + response_candidate_id

    Writing the assistant_message back onto the ChatTurn is what makes the
    completed Q&A part of the graph — so the next turn's chat_context_assembler
    reads it straight from the graph (no in-process history needed).

    This is the 'delivery' step for the chat channel. After this fires,
    response_dispatcher (Communication Pack) marks the candidate as 'sent'.
    """
    obj = event.payload.get("object", {})
    candidate_id = obj.get("id")
    data = obj.get("data", {})

    if data.get("status") != "approved":
        return
    # Channel-neutral on purpose: any interactive channel's conversation is
    # recorded on chat_turn objects. Candidates whose message doesn't map to
    # a turn (e.g. email drafts) simply find no turn below and no-op.

    message_id = data.get("message_id")
    response_content = data.get("content") or ""

    # Resolve the ChatTurn to patch. The _MESSAGE_TO_TURN cache is the hot path;
    # if it misses (e.g. after a restart), fall back to the graph view — the
    # turn records its comm_message_id, so the mapping is always recoverable.
    turn_id = _MESSAGE_TO_TURN.get(message_id)
    if not turn_id:
        for o in ctx.view.objects():
            if o.type == "chat_turn" and o.data.get("comm_message_id") == message_id:
                turn_id = o.id
                break

    if turn_id:
        try:
            graph.patch_object(turn_id, {
                "assistant_message": response_content,
                "response_candidate_id": candidate_id,
            })
        except Exception:
            pass


# ================================================================ BEHAVIORS registry

# Registration order is execution order. chat_context_assembler,
# chat_profile_context and chat_memory_context must all run BEFORE
# chat_llm_responder so the ChatContext (conversation memory), MemoryContext
# (long-term memory) and ProfileContextView (identity) — and their
# provides_context_for edges — exist when the responder's depth-1 view is built
# and the LLM is called. chat_memory_proposer (the WRITE path) can run anytime;
# it is grouped with the context behaviors for readability.
BEHAVIORS = [
    chat_ingester,
    chat_context_assembler,
    chat_profile_context,
    chat_memory_proposer,
    chat_memory_context,
    chat_llm_responder,
    chat_deflection_responder,
    chat_responder,
]
