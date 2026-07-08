# Communication Pack v0.2

Channel-neutral communication semantic layer for all ActiveGraph packs.

## Purpose

Communication Pack owns the **semantic layer** of communication. It provides the shared
primitives that all channel adapters (Chat, Email, SMS, Voice) translate into. Domain packs
(VC, Research) respond to `comm_message` objects regardless of which channel produced them.

## Object Types

| Type | Description |
|---|---|
| `comm_thread` | Conversation thread (channel + subject + participants) |
| `comm_message` | Channel-neutral message (inbound or outbound) |
| `comm_intent` | Classified intent of a message (query/request/reply/notification/review/approval_request/unknown) |
| `comm_response_candidate` | Proposed response pending approval + dispatch. Lifecycle: draft → proposed → approved → sent/rejected |
| `comm_participant` | A participant in a thread with a role (sender/recipient/cc/observer) |

## Relation Types

| Relation | Source → Target | Description |
|---|---|---|
| `thread_contains` | comm_thread → comm_message | Thread contains a message |
| `intent_of` | comm_intent → comm_message | Intent describes a message |
| `response_to` | comm_response_candidate → comm_message | Response to a message |
| `participates_in` | comm_participant → comm_thread | Participant is in a thread |
| `derived_from_source` | comm_message → source | CommMessage derived from Core Source |
| `dispatched_to` | comm_response_candidate → comm_thread | Candidate dispatched to channel |

## Behavior Map

```
comm_message.created [direction=inbound]
  → intent_detector
      heuristic keyword/pattern classification
      creates: comm_intent + intent_of relation
      intents: query | request | reply | notification | review | approval_request | unknown

  → thread_tracker
      creates/resumes CommThread keyed by (channel, thread_id_hint)
      patches: comm_message.thread_id
      creates: thread_contains relation, comm_participant (sender)
      uses: _THREAD_REGISTRY (no graph.objects() scan)

comm_intent.created
  → intent_router
      when settings.intent_routes has a route for the intent kind AND
      confidence ≥ intent_route_min_confidence:
      creates: capability_call(status=proposed) + fulfills_intent relation
      [Tool Gateway policy/approval/execution governs it from there]

comm_response_candidate.created [status=approved]
  → response_dispatcher
      creates: dispatched_to relation (candidate → thread)
      patches: comm_response_candidate.status = "sent"
```

## Intent Classification

`intent_detector` uses heuristic keyword/pattern matching (no LLM required):

| Intent | Signal Examples |
|---|---|
| `query` | `?`, `what is`, `how do`, `tell me`, `explain` |
| `request` | `please`, `can you`, `draft`, `write`, `create`, `generate` |
| `reply` | `in reply to`, `as discussed`, `following up`, `re:` |
| `notification` | `fyi`, `just to let you know`, `heads up`, `update:` |
| `approval_request` | `approve`, `approval`, `permission`, `lgtm`, `sign off` |
| `review` | `review`, `take a look`, `feedback`, `thoughts on` |
| `unknown` | No signals found or confidence below threshold |

## Settings

```python
CommunicationSettings(
    intent_detection_mode="heuristic",    # "heuristic" or "llm"
    auto_create_threads=True,             # Auto-create CommThread on first message
    default_channel="chat",              # Default channel
    low_confidence_intent_threshold=0.5, # Below this → intent="unknown"
    intent_routes={},                    # intent kind → capability route (see below)
    intent_route_min_confidence=0.6,     # Min confidence to propose an action
    auto_dispatch_approved_responses=True,
    max_thread_participants=50,
)
```

## Usage

```python
from activegraph import Runtime, Graph
from packs.core import pack as core_pack
from packs.communication import pack as comm_pack, CommunicationSettings
from packs.communication.tools import create_comm_message_fn

rt = Runtime(Graph())
rt.load_pack(core_pack)
rt.load_pack(comm_pack, settings=CommunicationSettings())

msg = create_comm_message_fn(graph, channel="chat", content="What's the status?",
                              sender_ref="alice@example.com", direction="inbound")
rt.run_until_idle()
# → CommIntent + CommThread + CommParticipant in graph
```

## Composes With

- **Core Pack** (required): Source.sender_ref triggers Identity Pack integration
- **Identity Pack**: `principal_resolver` fires on `source.created` from channel adapters
- **Agent Profile Pack**: `ProfileContextView` consumed by LLM responder behaviors
- **Memory Gateway Pack**: memory retrieval in LLM responder views

## Design Notes

- `thread_tracker` uses a module-level `_THREAD_REGISTRY` (not `graph.objects()`) for fast thread resolution
- `intent_detector` is deterministic (no LLM) — suitable for production without API keys
- `response_dispatcher` does not perform actual HTTP delivery — channel pack responders handle that
- Call `clear_thread_registry()` between test fixtures

## Intent routing (deterministic action path)

`intent_router` turns detected intents into Tool Gateway proposals — the
zero-LLM half of "chat that can act". Configure a route per intent kind:

```python
CommunicationSettings(intent_routes={
    "request": {
        "provider_name": "helpdesk",
        "capability_name": "file_ticket",
        "risk_class": "low",            # default "medium"
        "input": {"queue": "inbox"},    # static kwargs (optional)
        "content_field": "text",        # message text lands here (default)
    },
})
```

The router only PROPOSES (`capability_call`, status=proposed, linked
`fulfills_intent` → the comm_intent). The gateway's policy check, approval
hold, credential injection, and audit govern everything after. With no
routes configured (default) or without the Tool Gateway loaded, intents
stay informational — no coupling.

## Reply gating helper

`gating.decide_reply(graph, sender_ref, reply_policy=...)` is the single
policy decision every channel adapter shares: `"open"` (all but blocked),
`"known"` (owner/admin/collaborator), `"owner_only"` (owner/admin). It is
behavior-safe (identity registry + get_object; no graph scans) and
fail-closed — an unverifiable sender under a restrictive policy is
deflected, with the reason in the returned dict. Adapters stamp the verdict
onto the `comm_message` they create so responders can match it in `where`.
See the Chat Pack for the reference wiring.
