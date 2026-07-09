# ActiveGraph Packs

A modular, open-source collection of ActiveGraph packs for building auditable,
event-sourced assistants. This directory is the heart of the repo — a **reference
showcase for how packs should be built and layered**.

## What is a Pack?

A pack is a self-contained bundle of object types, relations, behaviors, tools,
prompts, settings, fixtures, and docs for one area of responsibility. Packs compose
through shared graph state — a behavior emits an object, that write is an event,
and the event triggers a behavior in another pack. There are **no direct inter-pack
function calls and no central coordinator**.

For the full mental model, read **[docs/concepts.md](../docs/concepts.md)**.

## Core vs. Layered Packs

Every pack here is one of two kinds:

- **Core Pack** (`core`) — the universal substrate. Seven object types and seven
  relation types that every other pack speaks. It depends on nothing and stays
  deliberately minimal (no people, companies, claims, or documents — those belong
  to layered packs).
- **Layered packs** — everything else. Each builds on Core via `requires = ["core"]`
  and optionally cooperates with other packs via `integrates_with`. Layered packs
  bridge their domain outputs back to Core primitives so the rest of the system can
  treat them uniformly.

## Pack Index

All packs below are implemented at **v0.1** with deterministic, API-key-free
fixtures.

### Core

| Pack | Tier | Description |
|------|------|-------------|
| [`core`](core/) | core | Universal primitives: source, observation, task, action, artifact, memory_candidate, evaluation |

### Infrastructure (cross-cutting capabilities)

| Pack | Requires | Description |
|------|----------|-------------|
| [`tool_gateway`](tool_gateway/) | core | Normalizes, authorizes, and records all external capability calls |
| [`secrets`](secrets/) | core | Credential references, scopes, runtime injection (secrets never enter model context) |
| [`memory_gateway`](memory_gateway/) | core | Memory candidate acceptance, item storage, retrieval, and ranking |
| [`identity_auth`](identity_auth/) | core | Principal resolution, roles, permissions, auth context |
| [`agent_profile`](agent_profile/) | core | Agent goals, personality, standing instructions, owner preferences |
| [`entity`](entity/) | core | Canonical people, organizations, projects with dedupe and merge |

### Communication

| Pack | Requires | Description |
|------|----------|-------------|
| [`communication`](communication/) | core | Channel-neutral thread/message/intent/response primitives |
| [`chat`](chat/) | core, communication | Chat adapter — translates chat input into comm_message |
| [`email`](email/) | core, communication | Email adapter — ingest, draft, and approval-gated send |

### Domain (channel-agnostic verticals)

| Pack | Requires | Description |
|------|----------|-------------|
| [`research`](research/) | core | Paper, method, idea atom, hypothesis, experiment tracking |
| [`codebase`](codebase/) | core | Repo, file, issue, PR, architecture decision tracking |
| [`team_ops`](team_ops/) | core | Projects, assignments, milestones extending Core task |
| [`meeting`](meeting/) | core | Meeting ingestion, transcript, decisions, action items |

### Bridge

| Pack | Requires | Description |
|------|----------|-------------|
| [`bridges`](bridges/) | core | `diligence_core_bridge` — zero-ontology pack mapping the bundled ActiveGraph Diligence pack's outputs onto Core types |

## Pack Dependency Graph

```
kernel (activegraph runtime)
└── core                          # Universal primitives — requires nothing
    ├── tool_gateway              # Capability execution
    ├── secrets                   # Credential management
    ├── memory_gateway            # Memory lifecycle
    ├── identity_auth             # Principal resolution
    ├── agent_profile             # Agent identity
    ├── entity                    # Entity deduplication
    ├── communication             # Channel-neutral messaging
    │   ├── chat                  # Chat adapter
    │   └── email                 # Email adapter
    ├── research                  # Research domain
    ├── codebase                  # Codebase domain
    ├── team_ops                  # Team/Ops domain
    └── meeting                   # Meeting domain
```

## Standard Pack Layout

```
packs/<pack_name>/
  __init__.py          # Exports `pack` and `<Name>Settings`
  object_types.py      # Pydantic schemas + ObjectType/RelationType lists
  behaviors.py         # @behavior, @llm_behavior decorators
  tools.py             # @tool decorated functions (may be empty)
  settings.py          # Pydantic settings (all fields have defaults)
  prompts/             # .md files with TOML frontmatter (LLM behaviors only)
  fixtures/            # .yaml/.json scenario fixtures (run without an API key)
  README.md            # Behavior map, object types, usage examples
  CHANGELOG.md         # Version history starting at v0.1.0
```

Copy `_template/` to start a new pack:

```bash
cp -r packs/_template packs/my_pack
```

See **[CONTRIBUTING.md](../CONTRIBUTING.md#adding-a-new-pack)** for the full
step-by-step build guide and the open-source hygiene checklist.

## Key Design Rules

1. Packs compose through **graph state**, not function calls
2. **Core stays small** — only 7 universal primitives
3. **Behavior-local context** — no global prompt blobs
4. **Secrets never enter model context**
5. **Domain packs are channel-agnostic**
6. Every pack must include **fixtures** (demo without API keys)
7. Every pack must include a **behavior map** in its README

See [`replit.md`](../replit.md) for the full Core Rules and the 10 key invariants.
