# Chat Adapter Pack v0.3

Translates interactive chat input into channel-neutral CommMessage objects, with
**graph-native conversation memory**.

## Purpose

Chat Pack is the adapter between a chat interface and the Communication Pack's
semantic layer. It handles session continuity, per-turn context assembly, and
LLM-based response generation. With `llm_provider="mock"`, it runs entirely
without API keys (useful for fixtures and tests).

The defining principle is that **conversation memory lives in the graph**, not in
process memory. Prior turns are reconstructed from persisted graph objects on
every turn, so the conversation survives an API-server restart mid-session.

## Object Types

| Type | Description |
|---|---|
| `chat_input` | Raw input from chat interface. Entry point for the adapter. |
| `chat_session` | Persistent session grouping multiple ChatTurns |
| `chat_turn` | Single request-response exchange (user_message + assistant_message) |
| `chat_context` | Graph-native conversation memory assembled for one inbound message |

## Relation Types

| Relation | Source → Target | Description |
|---|---|---|
| `session_contains_turn` | chat_session → chat_turn | Session contains a turn |
| `turn_from_input` | chat_turn → chat_input | Turn created from input |
| `session_has_thread` | chat_session → comm_thread | Session linked to CommThread |
| `provides_context_for` | chat_context → comm_message | Context assembled for a message |

## Behavior Map

```
chat_input.created
  → chat_ingester
      resolves/creates ChatSession (by explicit session_id from the graph, else
        by user_ref via the in-process cache, else new)
      creates: source(kind=chat_message), comm_message(channel=chat, inbound)
      creates: chat_turn(user_message=content, turn_number=N)
      relations: derived_from_source, session_contains_turn, turn_from_input
      → (triggers thread_tracker in Communication Pack → CommThread)

comm_message.created [channel=chat, direction=inbound]
  → chat_context_assembler                 (runs BEFORE chat_llm_responder)
      reads prior turns from the session-anchored graph view (restart-safe)
      formats the most recent `max_context_messages` into a transcript
      creates: chat_context(transcript, turn_count)
      relations: provides_context_for (chat_context → comm_message)

  → chat_llm_responder
      depth-1 view around the message captures the linked chat_context
      the runtime serializes the view into the prompt (no hand-written prompts)
      if llm_provider="mock": deterministic stub response
      creates: comm_response_candidate(channel=chat, status=approved*)
      relations: response_to
      * auto_approve_responses=True by default

comm_response_candidate.created [channel=chat, status=approved]
  → chat_responder
      patches: chat_turn.assistant_message = content   (← now part of the graph,
        so the NEXT turn's chat_context_assembler reads it back)
      patches: chat_turn.response_candidate_id = candidate.id

  → response_dispatcher (Communication Pack)
      patches: comm_response_candidate.status = "sent"
```

## Conversation memory (graph-native)

The LLM only ever sees the serialized graph **view** — the runtime assembles every
prompt from it, and developers never hand-write prompt text. So for the model to
"remember" earlier turns, those turns must appear in the responder's view.

`chat_context_assembler` makes that happen the ActiveGraph way:

1. On each inbound message it builds a view anchored at the `ChatSession`
   (depth 1), which contains every `chat_turn` linked by `session_contains_turn`.
2. It excludes the current turn, keeps the most recent `max_context_messages`,
   and renders them into a `chat_context.transcript`.
3. It links the `chat_context` to the inbound message via `provides_context_for`,
   so `chat_llm_responder`'s existing depth-1 view captures it without widening.

Because every prior turn is read from **persisted graph objects** (not an
in-process dict), conversation context survives an API-server restart
mid-session. The `chat_context` object is also a first-class, inspectable record
of exactly what memory was shown to the model.

An alternative considered was simply widening `chat_llm_responder`'s view to reach
the session and its turns. That is simpler, but it pulls *every* turn (no
`max_context_messages` bound), scatters them as raw objects, and records nothing
about what context was used. See the long comment on `chat_context_assembler` in
`behaviors.py` for the full trade-off.

## Self-knowledge ("who are you?" / "what's your mission?")

When the **Agent Profile Pack** is loaded, the assistant can answer questions about
itself without any new pack or special-cased prompt. The mechanism reuses the same
"inject a context view, let the responder serialize it" seam as conversation memory:

```
comm_message.created [channel=chat, direction=inbound]
  → chat_profile_context                   (runs BEFORE chat_llm_responder)
      if system_prompt_override is set:
        injects a profile_context_view carrying ONLY that override text
        (metadata.origin=system_prompt_override) and skips the profile
      elif include_profile:
        assembles a profile_context_view from the default AgentProfile
        (name, mission, goals, standing instructions, personality)
      relations: provides_context_for (profile_context_view → comm_message)
```

Because the view is linked to the inbound message, `chat_llm_responder`'s existing
depth-1 view captures it and the runtime serializes the identity into the prompt —
the model then answers "who are you?" / "what's your mission?" in its own words. No
hardcoded answers, no intent classifier, no dedicated Q&A behavior.

**Precedence.** `system_prompt_override` and `include_profile` are independent knobs;
the override is the more specific, intentional setting, so it wins: an override (if
set) is always injected — even when `include_profile=False`. With no override,
`include_profile=True` injects the profile and `include_profile=False` injects
nothing (the responder falls back to its static system prompt).

**Zero-config default.** `build_assistant(...)` seeds a single default `AgentProfile`
(from `AgentProfileSettings`) when the store has none, so a brand-new assistant can
describe itself out of the box. Pass `seed_profile=False` to opt out. On a resumed
(persisted) runtime, the profile is rebuilt from replayed objects and seeded if the
store predates this feature — see `demo_server._build_runtime`.

**Design notes (for OSS readers).** This is deliberately minimal and unopinionated:
- It does **not** add a new object type or pack — it reuses `agent_profile`'s
  `ProfileContextView` and the chat responder's depth-1 view.
- `chat_profile_context` imports `agent_profile` lazily and no-ops if the pack
  isn't loaded, so the Chat Pack keeps zero hard dependency on it.
- The profile view is assembled **synchronously** in the behavior (not via a
  request→view event round-trip) because the responder fires in the same cascade;
  an event-driven view would arrive too late for the same batch.
- Alternatives considered (single static system prompt, a dedicated identity
  behavior, widening the responder view) are documented inline in `behaviors.py`.

## Session Continuity

`chat_ingester` resolves the session graph-first, so continuity is restart-safe:

1. Explicit `session_id` in `ChatInput`: resumes that exact `ChatSession` by
   reading it from the **graph** (`turn_number` continues from the persisted
   `turn_count`). This is what the Inspector UI sends, and it works even after a
   restart has cleared the in-process caches.
2. No `session_id`: resumes the user's session via the `_SESSION_REGISTRY` cache
   (a best-effort convenience), falling through to a new session on a cache miss.
3. Neither resolves: creates a new `ChatSession` + `CommThread`.

The in-process maps (`_SESSION_REGISTRY`, `_MESSAGE_TO_TURN`, `_MESSAGE_TO_SESSION`)
are hot-path caches only — never the source of truth. Every value they hold is
also recorded on graph objects, and behaviors fall back to graph lookups when a
cache misses.

## Settings

```python
ChatSettings(
    llm_provider="mock",           # "mock" | "openai" | "anthropic"
    model="gpt-4o-mini",           # Ignored for mock
    system_prompt_override=None,   # Override AgentProfile system prompt
    tool_allow_list=[],            # Gateway capability keys the LLM may call
    max_tool_turns=4,              # Cap on LLM↔tool round-trips per turn
    max_context_messages=10,       # Prior turns in LLM context
    include_memory=True,           # Include Memory Gateway context (if loaded)
    include_profile=True,          # Include AgentProfile context (if loaded)
    auto_approve_responses=True,   # Auto-approve chat responses (no owner gate)
)
```

## Agentic chat (tools in the reply loop)

With a non-empty `tool_allow_list`, the responder runs the runtime's native
LLM tool loop — but never with raw capabilities. Each allow-list entry is a
gateway capability key ("provider.capability"), translated at pack build
time into a Tool Gateway PROXY (`packs.tool_gateway.llm_tools`): the model's
call is recorded as a `capability_call`, policy-checked, credential-injected
and sanitized by the gateway. Auto-approvable calls return their result to
the model in the same turn; higher-risk calls are held and the model reports
`held_for_approval` instead of pretending they ran.

```python
from bundles import build_assistant
from packs.chat import ChatSettings
from packs.tool_gateway.capabilities import register_web_fetch_capability

register_web_fetch_capability()          # gateway capability web.fetch_url
rt = build_assistant(chat_settings=ChatSettings(
    tool_allow_list=["web.fetch_url"],
))
```

Because @llm_behavior binds its tool set at behavior construction, the
allow-list is applied by `packs.chat.build_pack(llm_tools=...)` — bundles do
this translation (see `bundles/assistant.py:_chat_pack_for`). An empty
allow-list (the default) is today's conversational-only pack, unchanged.

Provider boundary (`llm.py`): `ProviderCompat` sanitizes pack-scoped tool
names on the wire ('pack.tool' → 'pack__tool'; OpenAI/Anthropic reject
dots) and maps response tool calls back to canonical names;
`OpenAICompatProvider` translates params for reasoning-model families.
`FallbackChatProvider` degrades plain-chat failures to an instructive reply
that names the real error, and re-raises mid-tool-loop failures rather than
corrupting the loop with canned text.

## Demo: 3-Turn Conversation

```python
from activegraph import Runtime, Graph
from packs.core import pack as core_pack
from packs.communication import pack as comm_pack
from packs.chat import pack as chat_pack, ChatSettings
from packs.chat.tools import submit_chat_input_fn

g = Graph()
rt = Runtime(g)
rt.load_pack(core_pack)
rt.load_pack(comm_pack)
rt.load_pack(chat_pack, settings=ChatSettings(llm_provider="mock"))

# Turn 1
submit_chat_input_fn(g, user_ref="alice@example.com", content="What is the status of the deal?")
rt.run_until_idle()

# Turn 2 (auto-resumes by user_ref)
submit_chat_input_fn(g, user_ref="alice@example.com", content="Please draft a summary.")
rt.run_until_idle()

# Turn 3
submit_chat_input_fn(g, user_ref="alice@example.com", content="What are the next steps?")
rt.run_until_idle()

# Inspect turns
turns = list(g.objects(type="chat_turn"))
for t in turns:
    print(f"Turn {t.data['turn_number']}:")
    print(f"  User: {t.data['user_message']}")
    print(f"  Assistant: {t.data['assistant_message']}")
```

## Composes With

- **Core Pack** (required): source creation
- **Communication Pack** (required): CommMessage, CommThread, intent_detector, thread_tracker
- **Identity Pack** (optional): source.sender_ref triggers principal_resolver
- **Agent Profile Pack** (optional): ProfileContextView in LLM context when `include_profile=True`; powers self-knowledge ("who are you?") via `chat_profile_context` (see above)
- **Memory Gateway Pack** (optional): memory retrieval when `include_memory=True`

## Notes

- `clear_session_registry()` and `clear_thread_registry()` between test fixtures
- `reset_mock_response_idx()` for reproducible mock responses
- Real LLM integration is wired but requires API key and non-mock provider setting
