# Personal-Assistant Upgrade Plan

**Date:** July 7, 2026
**Input:** the hands-on experience report (`activegraph-experience-report.md`, produced by an external
agent evaluation against OpenClaw/Hermes-class assistants; not committed to this repo) — every claim
re-verified against this codebase and against the installed `activegraph` 1.2.0 runtime source.
**Goal:** upgrade `activegraph-packs` from "reference architecture with a chat demo" to "a personal
assistant you can actually run" — while keeping the property that makes this repo worth existing:
**every capability is an elegant, graph-coordinated pack, not a bolted-on service.**

---

## Part 1 — Report verification

Every substantive claim in the experience report was checked against the code. Verdicts:

| # | Report claim | Verdict | Evidence |
|---|---|---|---|
| 1 | Chat pipeline has no tools wired; runtime supports a native tool loop but chat doesn't use it | **CONFIRMED** | `chat_llm_responder` is an `@llm_behavior` with no `tools=` (`packs/chat/behaviors.py:694`); the runtime supports `tools=` / `max_tool_turns` (`activegraph/behaviors/decorators.py:219`) |
| 2 | `comm_intent` detection is decorative — nothing routes intents to the tool gateway | **CONFIRMED** | `intent_detector` creates `comm_intent` (`packs/communication/behaviors.py:138`); no behavior in any pack has `where={"object.type": "comm_intent"}`; only fixtures read it |
| 3 | No permission gating on the respond path — strangers get full replies | **CONFIRMED, worse than reported** | No principal check anywhere on `comm_message → chat_llm_responder → chat_responder → response_dispatcher`. Additionally, `chat_profile_context` **hardcodes `audience_role="owner"`** for every requester (`packs/chat/behaviors.py:420,448`), so even agent_profile's owner/stranger content shaping is bypassed on chat |
| 4 | Pack-scoped tool names (`pack.tool`) break OpenAI function calling; the 400 is misreported as a network error | **CONFIRMED (runtime bug, not this repo)** | Names pass verbatim to the API (`activegraph/llm/openai.py:365`, no sanitization); `_classify_provider_exception` returns `llm.network_error` for everything that isn't a rate limit or timeout — including 400s and auth failures (`openai.py:404-420`, a documented closed taxonomy) |
| 5 | No `@tool` signature validation; no parameter-schema inference from type hints | **CONFIRMED (runtime)** | `activegraph/tools/decorators.py` stores `fn` unchecked; `input_schema=None` → empty parameters schema shown to the model |
| 6 | OpenAI provider unconditionally sends `max_tokens`/`temperature` (breaks newer reasoning-model families) | **CONFIRMED (runtime)** | `activegraph/llm/openai.py:165-166` |
| 7 | `rt.pending_approvals()` and the gateway hold state are two sources of truth | **CONFIRMED, with a correction** | The second source is **the graph**, not demo-server bookkeeping (the server tracks no approvals at all). `policy_enforcer` writes `capability_approval` graph objects directly (`packs/tool_gateway/behaviors.py:109-145`); `rt.pending_approvals()` reads a separate in-memory queue fed only by `ctx.propose_object`, which **no pack uses**. Worse than reported: a call held at `status="policy_checking"` has **no approval path at all** — no behavior, no tool, no HTTP endpoint can ever advance it |
| 8 | No sessions listing API; single-threaded one-shot server; no streaming | **CONFIRMED** | stdlib `HTTPServer` (not threading), `packs/demo_server.py:1316`; full endpoint audit found no session enumeration or approval endpoints |
| 9 | Questions get memorized ("What's my favorite color?" stored as guidance) | **CONFIRMED (category label corrected)** | No interrogative filter on either write path. The quoted example matches the `"my favorite"` cue and is stored as a **preference** (not instruction) with confidence 0.8 (`packs/chat/behaviors.py:483-514`); keyword priority in Core's `_infer_category` means "Should I always…?" **does** become an instruction (`packs/core/behaviors.py:80,100`) |
| 10 | Third-party email content memorized "as if the owner had said it" | **PARTIAL — admission confirmed, attribution refuted** | Inbound third-party text is memorized with **no provenance/direction filter** (Core's `observation_extractor` fires on any `source.created`). But `memory_writer` derives `subject_ref` from the source's `sender_ref` (`packs/memory_gateway/behaviors.py:63-81`), so the memory is correctly attributed to the *sender* and, under default subject scoping, does **not** surface in the owner's recall. The real gap is *admission* (third-party content becomes memory at all), not attribution |
| 11 | Recall retrieved the query as its own best match | **REFUTED for same-turn, but fragile** | The write path only *proposes* a candidate; promotion to a stored item happens in later event cascades, after the synchronous read. No self-match is possible in the same turn — but only because of cascade timing. There is no explicit exclusion guard, and an identical sentence stored in an earlier turn ranks 1.0 |
| 12 | No scheduler/heartbeat, no channel connectors | **CONFIRMED** | The runtime's `DelayedQueue` schedules by *event count* (`activate_after=N`), not wall-clock; nothing in the repo emits time-based events. The only channel is HTTP-injected chat; the email pack has no IMAP/SMTP |

**New findings from this review** (not in the report):

- `MemoryGatewaySettings.auto_accept_categories` is **cosmetic** — it only appends a sentence to the
  evaluation rationale; the docstring promises category-based auto-accept the code doesn't implement
  (`packs/memory_gateway/behaviors.py:126`).
- `FallbackChatProvider` swallows **every** provider exception into a canned mock reply
  (`packs/chat/llm.py:209-213`). Combined with the runtime misclassifying 400s, a config error is
  double-masked. This wrapper will also silently break a tool loop (the mock can't emit tool calls),
  so it must become tool-aware before Phase 1.
- The `where=` clause on the respond path cannot express "principal is owner" because the principal
  is a *different object* joined by a `resolves_to` edge — gating must be a behavior-body check or a
  gatekeeper behavior, not a declarative predicate. This shapes the Phase 2 design.

**Bottom line of the review:** the report is accurate where it matters. The trust layer (audit,
credential hygiene, subject-scoped memory, risk-tiered policy) genuinely works; the assistant
chassis (act, reach, wake, gate) genuinely does not exist yet. The two corrections (memory
attribution; where the approval split actually lives) change the *fix*, not the priority.

---

## Part 2 — Design constraints

The plan must not win capability by sacrificing the properties that differentiate this repo. Four
invariants shape every item below (from `docs/concepts.md` / `activegraph-direction-report.md` §25):

1. **Model proposes; runtime disposes.** The LLM never executes anything directly — every external
   effect is a `capability_call` that passes policy, credential injection, and sanitization.
2. **Packs compose through graph state, not function calls.** No coordinator, no main loop. A
   scheduler is a *frame emitter*, not an orchestrator.
3. **Packs degrade gracefully.** Every new integration is `integrates_with`, checked at runtime,
   with a working fallback.
4. **Fixtures run with no API key.** Every new behavior ships a deterministic fixture; wall-clock
   and network live at the edge, injected like `chat_input` already is.

A fifth, repo-specific constraint: **runtime bugs get upstream issues + pack-side shims.** The
tool-name bug, error taxonomy, signature validation, schema inference, and parameter compatibility
all live in the `activegraph` PyPI package, not here. This repo ships seam-level workarounds now and
files the runtime fixes separately (Part 4).

---

## Part 3 — The plan

Six phases. 0–3 are the difference between "reference architecture" and "personal assistant";
4–5 are the polish that makes it credible day-to-day. Each phase lists the packs touched, the new
graph vocabulary, and its acceptance fixture.

### Phase 0 — Close the trust loop (hardening; small)

The audit/approval story is the repo's headline, and it currently has a hole: a high-risk call
enters `policy_checking` and is unreachable forever. Fix trust before adding reach.

**0.1 Graph-native approval resolution** (`tool_gateway`)
- New tools: `approve_capability(call_id, approver_ref, note)` → creates the `capability_approval`
  object (the existing `call_executor` trigger — no new execution path) + `approved_by` relation;
  `deny_capability(call_id, approver_ref, reason)` → patches the call to `status="denied"` and
  creates a `capability_denial` object so refusals are as auditable as grants.
- New behavior `approval_recorder`: on `capability_denial.created`, links `denied_by` and closes the
  loop in the trace.
- The graph is the single source of truth. `rt.pending_approvals()` (in-memory, non-persistent,
  unused by any pack) is documented as a runtime-level facility this repo does not use; upstream
  issue filed to either back it by graph state or rename it (Part 4).
- Approver identity: `approve_capability` requires an `approver_ref`; when `identity_auth` is loaded
  it must resolve to a principal with an approving role — approvals themselves are permission-checked.

**0.2 Demo-server approval + session surface** (`packs/demo_server.py`)
- `GET /approvals` — `capability_call` objects with `status="policy_checking"` (+ recent grants/denials).
- `POST /approvals/<call_id>` — `{decision: "approve"|"deny", note}` → calls the 0.1 tools.
- `GET /sessions` — enumerate `chat_session` objects (id, user_ref, turn_count, started_at, status).
  All three are thin graph reads/tool calls — no server-side state.

**0.3 Provider-boundary shim** (`packs/chat/llm.py`)
- `ProviderCompat` wrapper (composes with `FallbackChatProvider`):
  - **Tool-name sanitization:** `pack.tool` → `pack__tool` in tool definitions sent to OpenAI;
    reverse-mapped on returned `ToolCall`s. Canonical names stay canonical everywhere inside the
    graph and trace. (This unblocks Phase 1 with OpenAI regardless of the upstream fix.)
  - **Parameter compatibility:** per model family, translate `max_tokens` →
    `max_completion_tokens` and omit `temperature` where rejected (gpt-5/o-series).
  - **Honest failure text:** `FallbackChatProvider`'s canned note gains the actual exception class
    and message, so a 400 no longer reads as a network problem even before the runtime taxonomy is
    fixed. It also learns to *re-raise* when a tool loop is active (a canned reply cannot answer a
    tool-call turn; failing loudly is correct there).

**Acceptance fixture:** a high-risk `capability_call` is held, listed via the approvals surface,
approved, executes with credential injection, and its result is sourced — end to end in one fixture,
no API key. A second fixture denies and asserts the denial audit trail.

### Phase 1 — Agentic chat: chat that can act (the headline; medium)

Two complementary routes from conversation to capability — one LLM-driven, one deterministic — both
converging on the same gateway so nothing bypasses policy.

**1.1 Gateway-proxied LLM tools** (`tool_gateway`, new module `llm_tools.py`)

The runtime's native tool loop executes `@tool` functions directly, with no policy hook (verified in
`runtime.py:_dispatch` — validation, budget, and cache only). So the tools we hand the LLM are
**proxies into the gateway**, not raw capabilities:

- `as_llm_tool(provider_id, capability, *, risk_class, input_schema, description)` → returns a
  runtime `Tool` whose body:
  1. records a `capability_call` (`status="proposed"`) in the graph — every model-initiated action
     is first-class and auditable *before* anything runs;
  2. applies the same policy decision as `policy_enforcer` (factored into a shared
     `decide_policy()` so there is exactly one policy implementation);
  3. **auto-approvable** → executes via the same factored execution path `call_executor` uses
     (credential injection from `secrets`, output sanitization, `capability_result`, result
     sourcing) and returns the sanitized result to the model in the same tool turn;
  4. **held** → leaves the call at `policy_checking` and returns
     `{"status": "held_for_approval", "call_id": ...}` — the model tells the user it needs
     sign-off; the Phase 0 approval surface resumes it; the eventual `capability_result` →
     `source` cascade can trigger a follow-up reply.
- This preserves *model proposes / runtime disposes* inside a synchronous tool loop: the LLM never
  touches an unrecorded, un-policied, un-sanitized capability, and the trace shows
  `llm.requested → tool.requested → capability_call → capability_approval → capability_result → tool.responded`.

**1.2 Wire the chat responder** (`chat`)
- `ChatSettings.tool_allow_list` (default: `["tool_gateway.web_fetch_capability"]`-class low-risk
  reads; empty list = today's conversational-only behavior, so this is opt-out clean).
- `chat_llm_responder` gains `tools=` built from the allow-list at pack-assembly time and a raised
  `max_tool_turns`. Implementation note: `@llm_behavior` binds tools at decoration, so the bundle
  factory (`build_assistant`) resolves the allow-list and constructs the responder via a small
  factory — settings stay the single knob, and fixtures pin a deterministic tool set.
- Recorded-LLM fixtures cover: tool call → grounded answer; held call → "waiting for approval"
  reply; tool error → honest failure reply.

**1.3 Deterministic intent routing** (`communication`, `integrates_with tool_gateway`)
- New behavior `intent_router`: on `comm_intent.created` with `intent="request"` (or
  `approval_request`) above a confidence floor, consult a small registry of
  *intent → capability templates* (`CommunicationSettings.intent_routes`) and emit a
  `capability_call` proposal linked `fulfills_intent → comm_intent`.
- This makes intent detection functional even in mock mode (and is the fixture-friendly path);
  the LLM route (1.1) and the intent route converge on identical gateway objects. `comm_intent`
  stops being a dead-end leaf.

**Acceptance fixture:** cross-pack scenario — inbound chat "fetch the pricing page and summarize
it" → recorded LLM emits a tool call → gateway records/policies/executes → grounded reply cites the
sourced result; the same scenario with a high-risk tool asserts the held-and-resumed path.

### Phase 2 — Reply gating: identity on the respond path (small)

Today identity gates memory and (nominally) actions, but anyone who can reach a channel gets full
answers with an owner-framed profile. Fix both halves:

**2.1 Audience-aware profile** (`chat`)
- `chat_profile_context` stops hardcoding `audience_role="owner"`: resolve the principal via the
  message's `resolves_to` edge (identity_auth is already computing it in the same cascade —
  registration order puts the resolver before the profile behavior) and pass the principal's actual
  role. agent_profile's existing owner/stranger content shaping starts working on chat for free.

**2.2 Respond policy** (`chat` + `communication`)
- `ChatSettings.reply_policy`: `"open"` (today's behavior, default for the demo), `"known"`,
  `"owner_only"`.
- The gate lives **inside `chat_llm_responder`, before the LLM call** — an unauthorized sender
  never spends a model call. Below-threshold senders get a `comm_response_candidate` generated from
  a deflection template (`ChatSettings.deflection_message`), `status="approved"`, flagged
  `metadata.gated=true` — the stranger still gets *a* reply (silence is bad UX and unauditable),
  but a bounded, non-conversational one, and the gating decision is a visible graph object.
- For channels beyond chat, the same check factors into a small
  `communication.gating.decide_reply(principal, settings)` helper any adapter can call — policy in
  one place, adapters stay thin.
- Tool access already keys off risk class; with 2.1 the principal is available to `decide_policy()`
  so per-role tool restrictions (stranger → no tools at all) become one line in the policy.

**Acceptance fixture:** owner gets a full reply with owner-scoped profile; a stranger gets the
deflection candidate with `gated=true`, no `llm.requested` event, and no tool access — asserted
from the trace.

### Phase 3 — `schedule` pack: wake up and act (medium)

The report's "nothing happens unless you push an HTTP request" gap. The elegant shape: **the pack
owns no clock and no thread** — wall-clock lives at the edge exactly like chat input does, so the
pack stays pure-reactive and fixtures stay deterministic.

**New pack `packs/schedule/`** (`requires=["core"]`, `integrates_with=["communication", "tool_gateway", "memory_gateway"]`)
- **Object types:**
  - `schedule` — name, spec (`{kind: "interval"|"cron"|"once", ...}`), payload (a declarative
    template for the object to emit: a `comm_message`, a core `task`, or a `capability_call`
    proposal), `enabled`, `next_due_at`, `last_fired_at`.
  - `schedule_tick` — schedule_id, `fired_at`, dedup key. The tick **is** the event-first record of
    "time passed"; each tick opens its own frame (the sanctioned `memory_reflection_pass`-style
    scope from the direction report §9).
- **Behaviors:**
  - `tick_router` — on `schedule_tick.created`: instantiate the schedule's payload object, link
    `emitted_by → schedule`. Everything downstream (a chat message wakes the responder; a
    capability proposal wakes the gateway) is existing machinery — the scheduler composes, it
    doesn't orchestrate.
  - `schedule_bookkeeper` — advances `next_due_at` / `last_fired_at` after a tick.
- **Tools:** `create_schedule`, `list_due(now)`, `emit_due_ticks(now)` — the host driver calls
  `emit_due_ticks` with a timestamp; the tool creates `schedule_tick` objects for due schedules
  (idempotent via the dedup key). Passing `now` in keeps fixtures clock-free: a fixture injects
  ticks or calls the tool with synthetic times.
- **Driver:** the demo server gains a daemon thread — `emit_due_ticks(utcnow)` +
  `run_until_idle()` under the existing runtime lock every N seconds. ~20 lines, entirely at the
  edge. Any other host (cron, a worker, a Replit deployment) can drive the same tool.
- **Heartbeat:** a seeded default `schedule` ("heartbeat", interval) whose payload opens a
  maintenance frame — the natural home for follow-up checks and, later, the Phase 5 memory
  reflection pass. "Always-on" becomes a schedule row, not an architecture change.
- Agentic tie-in: `create_schedule` exposed through Phase 1's `as_llm_tool` (risk class low) makes
  "remind me tomorrow at 9" a one-turn chat interaction that is fully policy-governed and audited.

**Acceptance fixture:** create schedules (interval + once), call `emit_due_ticks` with synthetic
times, assert tick dedup, payload emission into a fresh frame, downstream behavior firing, and
bookkeeping — zero clocks, zero threads, zero keys.

### Phase 4 — `telegram` pack: one real channel (medium)

Telegram is the cheapest credible always-on channel (single bot token, long-polling, no webhook
infra needed). The pack is a **pure adapter** in the §15 mold — and its outbound path doubles as
the showcase that *sending a message is itself a governed capability*.

**New pack `packs/telegram/`** (`requires=["core", "communication"]`, `integrates_with=["identity_auth", "tool_gateway", "secrets", "chat"]`)
- **Inbound:** `telegram_update` object (raw update, injected by the driver) → `telegram_ingester`
  behavior → `source(kind=telegram_message)` + `comm_message(channel="telegram",
  sender_ref="telegram:<user_id>")`, mirroring `chat_ingester` (per-chat `thread_id_hint`). The
  existing cascade — principal resolution, profile context, memory, responder, Phase 2 gating —
  runs unchanged; the responder's `where` widens from `channel=="chat"` to a configured channel set
  (one settings-driven predicate change in `chat`, which becomes the generic *interactive-channel*
  responder it already almost is).
- **Identity:** `TelegramSettings.owner_user_id` seeds the owner principal binding
  (`telegram:<id>` → owner) through identity_auth's existing registration tools; unknown senders
  resolve to stranger principals and hit the Phase 2 gate. This is where reply gating stops being
  theoretical: the bot is reachable by anyone who finds it.
- **Outbound:** `telegram_dispatcher` — on `comm_response_candidate(channel="telegram",
  status="approved")` → emits a `capability_call` (`provider="telegram"`,
  `capability="send_message"`, risk `medium`) rather than calling the API. The gateway executes it
  with `TELEGRAM_BOT_TOKEN` injected by `secrets` at execution time, sanitizes, records the result,
  and the dispatcher marks the candidate `sent` on `capability_result`. Every outbound message is
  policy-checked, credential-hygienic, and in the audit trail — the property OpenClaw-class tools
  can't show.
- **Driver:** `python -m packs.telegram.poller` — long-polls `getUpdates`, injects
  `telegram_update` objects (directly in-process, or via a new `POST /telegram/update` on the demo
  server). Edge component; the pack itself makes no network calls outside the gateway.
- **Fixtures:** inject synthetic updates; recorded gateway results for outbound. No token needed.

**Email note:** the email pack already has threading/dedup/draft objects; IMAP/SMTP is the same
adapter pattern (poller driver in, `capability_call` out) and is scheduled after Telegram proves
the mold rather than in parallel.

**Acceptance fixture:** synthetic update from the owner → full reply dispatched through a gateway
call; synthetic update from a stranger → deflection; outbound send asserted to carry no token
material anywhere in the graph.

### Phase 5 — Memory curation: remember the right things (small–medium)

The plumbing (scoping, persistence, dedup, promotion) verified sound; the judgment layer needs
work. Principle: **admission decisions concentrate in the evaluator** — the governance point the
architecture already designates — rather than scattering filters across proposers.

- **5.1 Interrogative filter (proposers).** Both write paths skip questions: cheap terminal-`?` /
  leading-interrogative check in `chat_memory_proposer`; in Core's `_infer_category`, move the
  question check **ahead of** keyword categories so "Should I always…?" classifies as `question`
  and is dropped by the existing category allow-list. (Proposer-side because a question is not a
  weaker candidate — it is not a candidate.)
- **5.2 Provenance admission policy (evaluator).** `MemoryGatewaySettings.admission_policy`:
  by default, candidates whose provenance is inbound third-party content (source direction +
  sender vs. known principals, via the already-captured `sender_ref`) are **rejected with a
  rationale** — auditable, not silent — unless the category is a fact *about the sender*
  (entity-style knowledge stays useful). Owner statements and assistant-confirmed decisions admit
  as today. This fixes the confirmed half of report claim 10 at the right layer.
- **5.3 Make `auto_accept_categories` real or remove it.** Implement the documented behavior
  (category-based threshold relief) — resolving the docstring/code mismatch found in review.
- **5.4 Same-turn recall guard.** `retrieve_by_query` gains an `exclude_frame_id` filter and the
  chat read path passes the current frame — converting the timing-based protection (claim 11) into
  a designed guarantee that survives future reordering.
- **5.5 LLM evaluator as swap-in showcase (optional).** An `@llm_behavior` variant of
  `candidate_evaluator` emitting the same `evaluation` objects, enabled by settings, with recorded
  fixtures — demonstrating that the candidate → evaluation → item lifecycle is a real seam. Runs
  naturally inside the Phase 3 heartbeat frame as a periodic reflection pass over recent
  candidates.

**Acceptance fixture:** "What's my favorite color?" produces no memory; "My favorite color is
green" produces one; a third-party email request is rejected-with-rationale at evaluation; recall
during the same frame never returns items born in it.

### Phase 6 — Server chassis (small, mostly deferred)

- **Threading:** `ThreadingHTTPServer` + the existing runtime lock around mutation; read-only
  endpoints (`/summary`, `/graph`, `/trace`, `/approvals`, `/sessions`) stop queueing behind chat
  turns.
- **Streaming:** deferred deliberately. The provider protocol is non-streaming (runtime-level;
  upstream issue filed). Interim: an SSE endpoint that streams *events* for a frame as they settle
  — which is honest about what the architecture is (event-sourced) instead of faking token
  streaming. Token streaming lands when the runtime protocol supports it.
- The demo server stays a demo server; anything beyond this belongs in a real host, not more
  stdlib-HTTP engineering.

---

## Part 4 — Upstream runtime issues (activegraph, PyPI)

Filed against the runtime package, each with the pack-side mitigation that de-risks waiting:

| Issue | Mitigation in this repo |
|---|---|
| Sanitize tool names at the provider boundary (`.` → `__`), keep canonical names internal | Phase 0.3 `ProviderCompat` wrapper |
| Split `llm.network_error` taxonomy: `llm.request_error` (4xx), `llm.auth_error` | Phase 0.3 honest fallback text carries the real exception |
| `@tool` registration-time signature validation (`(args, ctx)` contract) | Convention + review here; proxies from `as_llm_tool` are generated, so Phase 1 code is immune |
| Parameter-schema inference from type hints when `input_schema` is absent | `as_llm_tool` requires an explicit schema — never ships an empty one |
| Conditional `max_tokens`/`temperature` per model family | Phase 0.3 parameter translation |
| `rt.pending_approvals()` graph-backed (or renamed) — in-memory queue is invisible to pack-level approvals and doesn't survive `Runtime.load` | Phase 0.1 makes the graph the sole source of truth |
| Streaming provider protocol | Phase 6 SSE-over-events interim |

---

## Part 5 — Sequencing, sizing, and the definition of done

| Phase | Size | Depends on |
|---|---|---|
| 0 — Trust loop + shims | S (days) | — |
| 1 — Agentic chat | M | 0 |
| 2 — Reply gating | S | 0 (identity edge reuse) |
| 3 — `schedule` pack | M | — (parallel to 1–2) |
| 4 — `telegram` pack | M | 1, 2 |
| 5 — Memory curation | S–M | — (parallel) |
| 6 — Server chassis | S | 0 |

Ordering rationale vs. the report's top-10: the report puts agentic chat first; this plan puts the
**approval-loop closure** first because an agent that can act (Phase 1) with an approval hold that
can never be released (today's state) is worse than one that can't act — trust is this repo's
differentiator and it must be airtight *before* capability lands on top of it. The tool-name bug is
absorbed into Phase 0.3 rather than being its own item, and the report's items 5–10 map onto
Phases 5, 0.1/0.2, 2, 6, and Part 4 respectively.

**Definition of done — the day-in-the-life this plan must pass** (as fixtures, then live):

1. The owner messages the assistant on Telegram; it answers with memory and persona intact
   across a server restart.
2. A stranger messages the bot and gets a polite deflection; the gating decision is in the trace;
   no LLM tokens were spent.
3. "Look up X and summarize it" executes a low-risk capability auto-approved through the gateway,
   with the result sourced and cited.
4. "Email the memo to Bob" is held; the owner sees it in `/approvals`, approves; it executes with
   credentials injected at runtime and never present in the graph.
5. "Remind me tomorrow at 9am" creates a `schedule` through a governed tool call; the tick fires,
   opens a frame, and the reminder arrives on Telegram.
6. The heartbeat schedule runs a reflection pass; questions were never memorized; the third-party
   email request was rejected with a written rationale.
7. Every one of the above is reconstructible from `GET /trace` — because each ran through the same
   graph the Inspector already renders.

That scenario is the honest pitch upgraded: no longer "the governance layer OpenClaw should have
had," but *an assistant you can hand your credentials — that can prove what it did with them.*
