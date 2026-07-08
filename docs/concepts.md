# Concepts: Core and Layered Packs

This repository is a **prototyping ground and reference showcase** for one idea:
*how packs should be built and composed on top of [ActiveGraph](https://pypi.org/project/activegraph/).*
It is intentionally not a product. It exists so that pack authors have a concrete,
working example of the conventions, the layering model, and the coordination style
that make a multi-pack assistant coherent instead of a tangle of special cases.

The single most important idea to take away is the split between the **Core Pack**
and the **layered (domain) packs** that build on it.

---

## The substrate: an event-sourced object graph

ActiveGraph is a reactive object-graph runtime. There is no orchestrator and no
main loop that you write. Instead there are four primitives:

| Primitive | What it is |
|-----------|------------|
| **Object** | A typed node in the graph (a `source`, a `task`, an `email_thread`, …) |
| **Relation** | A typed edge between objects (`grounds`, `produces`, `derived_from`, …) |
| **Behavior** | A reactive handler that fires when matching objects/events appear |
| **Tool** | A callable capability a behavior may invoke (an API, an MCP call, …) |

Every mutation is appended to an event log first, then applied to graph state.
That log is the source of truth — you can replay it, audit it, and inspect every
decision the system made. The Inspector UI in this repo is just a window onto that
log and the resulting graph.

**Coordination is emergent.** A behavior in one pack writes an object; that write
is an event; that event triggers a behavior in another pack. Packs never call each
other's functions directly and there is no central coordinator deciding who runs
next. This is the property that lets you add or remove a pack without rewiring the
rest of the system.

---

## Two kinds of packs

A **pack** is a self-contained bundle of object types, relations, behaviors, tools,
prompts, settings, fixtures, and docs for one area of responsibility. Every pack in
this repo falls into exactly one of two tiers.

### 1. The Core Pack — the universal substrate

`packs/core/` is the base layer. It defines **7 object types** and **7 relation
types** that form the shared vocabulary every other pack speaks:

```
source · observation · task · action · artifact · memory_candidate · evaluation
```

Core is **deliberately minimal**. It has no dependencies (`requires = []`) and it
deliberately does *not* contain people, companies, claims, evidence, documents, or
any domain noun. Those belong to layered packs. The discipline here is the whole
point: if Core grows into a universal ontology, every domain pack ends up fighting
it. Keeping Core to seven primitives keeps it a *lingua franca* rather than a
straightjacket.

> **Rule:** never add a domain concept to Core. See the "Core stays small"
> invariant in [`replit.md`](../replit.md).

### 2. Layered packs — domain capability on top of Core

Every other pack is a **layered pack**. It builds on Core and, often, on other
infrastructure packs. Layering is expressed through two documented dependency
declarations — recorded in each pack's README and `__init__.py` and respected
through load order (they are conventions, not enforced kwargs on `Pack(...)`):

- **`requires`** — hard dependencies the pack assumes are already loaded; Core is
  the universal one. Almost every layered pack lists `requires = ["core"]`;
  communication adapters like `chat` and `email` also require `communication`
  (and the messenger adapters `telegram`/`whatsapp` additionally require `chat`,
  whose session/memory/gating machinery they reuse).
- **`integrates_with`** — optional packs that *improve* behavior but are not
  mandatory. A pack must still function (degrade gracefully) when they are absent.

Layered packs split further by intent:

| Tier | Packs | Role |
|------|-------|------|
| **Core** | `core` | The 7 universal primitives. Depends on nothing. |
| **Infrastructure** | `tool_gateway`, `secrets`, `memory_gateway`, `identity_auth`, `agent_profile`, `entity`, `schedule` | Cross-cutting capabilities every assistant needs: tool execution, credentials, memory lifecycle, identity, agent persona, entity dedupe. |
| **Communication** | `communication`, `chat`, `telegram`, `whatsapp`, `email` | Channel-neutral messaging plus channel adapters (`chat` is the conversation engine the messenger adapters reuse). |
| **Domain** | `research`, `vc`, `codebase`, `team_ops`, `meeting` | Specific verticals. Channel-agnostic — they consume infrastructure and communication, never own them. |
| **Bridge** | `bridges` (diligence_core_bridge) | Zero-ontology pack mapping a third-party pack's outputs onto Core types. |

The dependency picture:

```
kernel (activegraph runtime)
└── core                          # universal primitives — requires nothing
    ├── tool_gateway              # integrates_with: secrets
    ├── secrets                   # integrates_with: tool_gateway
    ├── memory_gateway            # integrates_with: tool_gateway
    ├── identity_auth             # integrates_with: entity
    ├── agent_profile             # integrates_with: identity_auth
    ├── entity                    # integrates_with: identity_auth
    ├── communication             # integrates_with: identity_auth, agent_profile, memory_gateway
    │   ├── chat                  # requires: core, communication
    │   └── email                 # requires: core, communication
    ├── research                  # integrates_with: entity, memory_gateway
    ├── vc                        # requires: core, communication
    ├── codebase                  # integrates_with: identity_auth, team_ops
    ├── team_ops                  # integrates_with: identity_auth, codebase, meeting
    └── meeting                   # integrates_with: team_ops, identity_auth, communication
```

---

## Why domain packs map back to Core

The reason layering works is that domain packs **bridge their outputs to Core
primitives**. A `vc` pack might create a domain object for a founder email, but it
also creates Core `observation`s and `artifact`s connected by relations like
`derived_from`. That has two payoffs:

1. **Auditability** — anything in the system, regardless of which pack produced it,
   is reachable through the same seven Core types and the same event log.
2. **Composition** — an infrastructure pack like `memory_gateway` only has to
   understand Core `memory_candidate`s. It never needs to know `vc` exists. New
   domain packs get memory, tools, and identity *for free* by speaking Core.

The `bridges` pack is the purest illustration: it has **no object types of its
own**. It subscribes to a third-party pack's events and emits parallel Core objects,
so the rest of the system can treat foreign objects as first-class citizens.

---

## Bundles are not a third tier

A **bundle** (`bundles/`) is *not* a pack. It has no ontology. It is just a named
list of packs in a sensible load order plus a factory function with good defaults —
a convenience for standing up a working assistant without loading each pack by hand.
See [bundles/README.md](../bundles/README.md).

---

## The invariants that hold it together

These are the rules that keep the layering honest. The full list lives in
[`replit.md`](../replit.md); the ones that matter most for understanding the model:

1. **Core stays small** — only the 7 universal primitives, forever.
2. **Packs compose through graph state, not function calls** — no direct calls, no
   central coordinator.
3. **Packs degrade gracefully** — hard-require only what you truly need; everything
   else is `integrates_with`.
4. **Domain packs are channel-agnostic** — they never own communication, identity,
   or tool execution; they consume those capabilities if present.
5. **Secrets never enter model context** — credentials are references injected only
   at tool-execution time.
6. **Memory is candidate-first** — durable memory is proposed, evaluated, then
   written; never written directly.

---

## Where to go next

- [Authoring a pack](../CONTRIBUTING.md#adding-a-new-pack) — the step-by-step build
  guide and the open-source hygiene checklist.
- [Architecture](architecture.md) — the demo stack (UI / API / Python runtime) and
  the Inspector UI.
- [Core Pack reference](../packs/core/README.md) — the seven primitives in detail.
- [Long-term memory](long-term-memory.md) — a worked example of cross-pack
  composition (Chat → Memory Gateway) with swappable seams.
