# Chat Pack Changelog

## v0.5.0 — Recall embedding rides the recorded runtime path (2026-07-10)

### Changed
- chat_memory_context passes its behavior ctx to retrieve_memories_fn, so
  the recall query embedding goes through the runtime's recorded
  ctx.embed path (embedding.requested/responded events, replayable) when
  the runtime has an embedding provider (P10). Hosts without one see the
  exact previous behavior.

## v0.4.0 — Provider shims retired (activegraph >=1.3) (2026-07-08)

### Removed
- `ProviderCompat` (pack-scoped tool-name wire sanitization + reverse map)
  and the OpenAI reasoning-family parameter shim (`_OpenAIParamShim`,
  `OpenAICompatProvider`). CONTRACT v1.3 #3 moved both concerns into the
  runtime's provider boundary (`activegraph.llm.wire`, per-family request
  params in `OpenAIProvider`, for BOTH providers including echoed
  assistant turns). Retirement proof: `tests/test_provider_compat.py` now
  points at the runtime surfaces and must keep passing.
- `@tool` signature defaults for v1.3 registration-time validation
  (`submit_chat_input`).

### Kept, with reason
- `FallbackChatProvider` stays: it is mock-mode UX (a plain chat failure
  degrades to an instructive reply naming the fix), never a runtime
  workaround. The v1.3 error-taxonomy split (`llm.auth_error` /
  `llm.request_error` terminal) makes its messages more accurate for free.

## v0.3.0 — Agentic chat + provider-boundary compatibility (2026-07-08)

### Added
- **Agentic responder.** `make_llm_responder(tools=..., max_tool_turns=...)`
  builds `chat_llm_responder` with gateway proxy Tools wired into the
  runtime's native LLM tool loop; `build_pack(llm_tools=...)` swaps it into
  the pack. `ChatSettings.tool_allow_list` (capability keys) and
  `ChatSettings.max_tool_turns` are the knobs — bundles translate the
  allow-list into proxies (see bundles/assistant.py). Every model-initiated
  action is still recorded, policy-checked, credential-injected, and
  sanitized by the Tool Gateway; chat never touches a raw capability.
  Empty allow-list (default) = conversational-only, unchanged.
- **ProviderCompat** (llm.py): sanitizes pack-scoped tool names at the
  provider wire boundary (`pack.tool` → `pack__tool`; OpenAI and Anthropic
  both reject dots) and maps response tool calls back to canonical names.
  Canonical names everywhere inside the graph and trace.
- **OpenAICompatProvider** (llm.py): translates `max_tokens` →
  `max_completion_tokens` and drops `temperature`/`top_p` for
  reasoning-model families (gpt-5 / o-series), which reject them.
- Tests: `tests/test_provider_compat.py`, `tests/test_agentic_chat.py`
  (scripted-provider end-to-end: tool call → gateway → grounded reply).

- **Reply gating — identity on the respond path.**
  `ChatSettings.reply_policy` ('open' | 'known' | 'owner_only', default
  'open') is decided at ingestion via `communication.gating.decide_reply`
  and stamped on the comm_message (`metadata.reply_gate` / `reply_gate_reason`
  / `sender_role`). `chat_llm_responder` matches `reply_gate == "open"`
  declaratively in its `where`, so gated senders never trigger the LLM call;
  the new `chat_deflection_responder` serves them
  `ChatSettings.deflection_message` (bounded template, `metadata.gated=true`).
  Restrictive policies are fail-closed — seed the owner via
  `bundles.seed_owner_principals`. Blocked principals are deflected under
  every policy. Context/memory behaviors skip deflected messages.
- **Audience-aware profile.** `chat_profile_context` no longer hardcodes
  `audience_role="owner"` for every requester — the sender's resolved role
  (stamped at ingestion) shapes the profile view, so strangers get the
  external-shaped view (mission suppressed) instead of the owner-framed one.
- Tests: `tests/test_reply_gating.py`, `tests/test_memory_curation.py`.
- **Memory write-path curation**: `chat_memory_proposer` skips
  interrogatives (punctuated or bare — "what's my favorite color" must not
  be memorized as a preference), and `chat_memory_context` passes the
  asking frame to recall so a memory born in the current turn can never
  answer it.

### Changed
- `FallbackChatProvider` now names the actual underlying error in its
  fallback reply (a 400 no longer reads like a network problem), degrades
  gracefully on first-call failures even when tools are offered, and
  re-raises MID tool-loop (canned text must not replace a grounded answer).

## v0.2.0 — Graph-native conversation memory (2026-06-04)

### Added
- `chat_context` object type — a first-class, inspectable record of the
  conversation memory assembled for one inbound message (transcript + turn_count).
- `provides_context_for` relation (`chat_context → comm_message`) — links the
  assembled memory to the message it was built for, so the responder's existing
  depth-1 view captures it without widening.
- `chat_context_assembler` behavior — on `comm_message.created (channel=chat,
  inbound)`, reads prior turns from the **session-anchored graph view**, keeps the
  most recent `max_context_messages`, renders a transcript, and creates the
  `chat_context`. Runs before `chat_llm_responder` in the behavior order.
- Fixtures: `run_multi_turn_recall_fixture` (graph-native, restart-safe recall)
  and `run_bounded_context_fixture` (verifies `max_context_messages` bound).

### Changed
- **Conversation memory is now graph-native.** Prior turns reach the LLM only via
  the serialized graph view, reconstructed from persisted objects on every turn —
  so a conversation survives an API-server restart mid-session. Replaced the
  process-local `_SESSION_TURN_HISTORY` side-channel (removed).
- `chat_ingester` resolves an explicit `session_id` from the **graph**
  (`turn_count`), making session continuity restart-safe. The in-process
  `_SESSION_REGISTRY` is now a best-effort cache, never the source of truth.
- `get_session_turns` tool now reads turns from the graph instead of a process dict.

### Fixed
- **`add_relation` argument order.** All chat-pack calls passed
  `(type, source, target)` but the API is `(source, target, type)`. The malformed
  relations had the type string as their `source`, which silently broke
  neighborhood traversal and views (the assembler saw no turns). Corrected all
  calls and the fixture assertion that had been written against the broken shape.

### Removed
- Dead `context_turn_count` write on `comm_response_candidate` — the field is not
  in that schema and was always dropped. The auditable count now lives on
  `chat_context.turn_count`.

## v0.1.0 — Initial release (2026-06-03)

### Added
- 3 object types: `chat_input`, `chat_session`, `chat_turn`
- 3 relation types: `session_contains_turn`, `turn_from_input`, `session_has_thread`
- 3 behaviors:
  - `chat_ingester` — on `chat_input.created`: resolves or creates `ChatSession`, creates `source(kind=chat_message)` + `comm_message(channel=chat, inbound)` + `chat_turn`; maintains `_SESSION_REGISTRY` for per-user session continuity without `graph.objects()` scans
  - `chat_llm_responder` — on `comm_message.created (channel=chat, inbound)`: assembles context (prior turns, profile view, memory), generates response via LLM or deterministic mock stub, creates `comm_response_candidate(channel=chat, status=approved)`
  - `chat_responder` — on `comm_response_candidate.created (channel=chat, status=approved)`: patches `chat_turn.assistant_message` and `chat_turn.response_candidate_id`
- `ChatSettings` with `llm_provider`, `model`, `system_prompt_override`, `max_context_messages`, `include_memory`, `include_profile`, `auto_approve_responses`
- Tool functions: `submit_chat_input_fn`
- `llm_provider="mock"` deterministic stub for fixture runs (no API key required)
- Fixture scenarios: 3-turn conversation, session continuity, mock LLM
- Full README with behavior map and session continuity docs

### Design decisions
- `chat_llm_responder` fires on `comm_message.created` (not `chat_turn.created`) so the Communication Pack's `intent_detector` and `thread_tracker` always run first
- Session continuity uses `_SESSION_REGISTRY` keyed by `user_ref` (or explicit `session_id`) — safe for re-entrant behavior context
- `chat_responder` patches the turn rather than creating a new object — the turn is the canonical request-response unit
- Clear between tests: `clear_session_registry()`, `reset_mock_response_idx()`
