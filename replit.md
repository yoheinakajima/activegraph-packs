# ActiveGraph Pack Library

A modular, open-source collection of ActiveGraph packs, bundles, and an inspector UI. Lets developers build auditable, event-sourced assistants with graph-visible communication, memory, tools, and workflows.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pip install -e ".[dev]"` — install activegraph and all packs in editable mode
- `python packs/demo_server.py` — run the ActiveGraph demo runtime (used by UI)
- `activegraph quickstart` — run the bundled Diligence pack demo (no API key needed)

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- Python 3.11+, activegraph[llm], Pydantic v2
- API: Express 5
- Validation: Zod (`zod/v4`), Pydantic
- Build: esbuild (CJS bundle)

## Where things live

```
packs/                    # All ActiveGraph packs (Python)
  core/                   # Core Pack v0.1 — universal primitives
  _template/              # Blank pack scaffold to copy from
bundles/                  # Pre-assembled pack collections
  assistant.py            # Base assistant bundle
  email_assistant.py      # Email-capable assistant
  research_bundle.py      # Research assistant
artifacts/
  api-server/             # Express API server (TypeScript)
  activegraph-ui/         # React inspector UI (TypeScript)
pyproject.toml            # Python project — activegraph dep + pack entry points
```

## Core Rules

These rules are **non-negotiable** for all packs in this repo. They encode the design philosophy from the ActiveGraph architecture doc and keep the codebase open-source-ready and composable.

### 1. The 10 Key Invariants (never violate)

1. **Event-first** — Every meaningful operation is represented as an event before it changes graph state or external state.
2. **Behavior-local context** — Behaviors declare the context they need through views. No global context blobs.
3. **Model proposes; runtime disposes** — The model proposes actions, tool calls, memory writes, and responses. ActiveGraph records, authorizes, executes, and observes.
4. **Secrets never enter model context** — Secrets are injected only into runtime/tool execution contexts via credential references.
5. **External capabilities go through Tool Gateway** — APIs, MCP, SDKs, local tools, browser tools, and skills all become ActiveGraph actions.
6. **Memory is candidate-first** — Durable memory must be proposed, evaluated, accepted, and then written/synced. Never write memory directly.
7. **Core stays small** — Core Pack defines universal primitives only: source, observation, task, action, artifact, memory_candidate, evaluation. Nothing else.
8. **Packs degrade gracefully** — Packs require only what they truly need and optionally integrate with others via `integrates_with`.
9. **Domain packs are channel-agnostic** — Research, Codebase, Meeting, and Team/Ops must not own communication, identity, or tool execution. They consume those capabilities if available.
10. **Behavior maps make packs understandable** — Every pack must expose a behavior map: event/object → behavior → output objects/actions → downstream behavior.

### 2. Pack Naming Conventions

- Pack directories: `snake_case` (e.g., `tool_gateway`, `agent_profile`, `team_ops`)
- Pack names in code: match directory name (e.g., `pack = Pack(name="tool_gateway", ...)`)
- Object types: `snake_case` nouns (e.g., `memory_candidate`, `comm_message`, `credential_ref`)
- Relation types: `snake_case` verbs or prepositions (e.g., `grounds`, `produces`, `derived_from`)
- Behaviors: `snake_case` verb phrases (e.g., `observation_extractor`, `entity_resolver`)
- Settings classes: `PascalCase` + `Settings` suffix (e.g., `CoreSettings`, `EmailSettings`)

### 3. Pack Structure Rules

Every pack **must** follow this layout:

```
packs/<pack_name>/
  __init__.py          # Exports `pack` and `<Name>Settings`
  object_types.py      # Pydantic schemas + ObjectType/RelationType lists
  behaviors.py         # @behavior, @llm_behavior, @relation_behavior decorators
  tools.py             # @tool decorated functions (may be empty)
  settings.py          # Pydantic settings class with all fields having defaults
  prompts/             # .md files with TOML frontmatter (if LLM behaviors exist)
  fixtures/            # .yaml or .json scenario fixtures for testing without LLM
  README.md            # Required: behavior map, object types table, usage examples
  CHANGELOG.md         # Required: starts at v0.1.0
```

### 4. Behavior Specification Rules

Every behavior **must** declare:
- `name` — unique within the pack, `snake_case`
- `on` — list of event types that trigger it (e.g., `["object.created"]`)
- `where` — filter dict narrowing which events qualify (e.g., `{"object.type": "source"}`)
- `creates` — list of object types this behavior produces (for documentation and behavior maps)
- `description` — one sentence explaining what it does and why

LLM behaviors additionally must declare:
- `output_schema` — Pydantic model for structured output
- `tools` — list of tools the behavior may call (empty list if none)
- `deterministic=True` — always set for fixture-backed behaviors

### 5. Inter-Pack Dependency Rules

- `requires` — hard dependencies; the runtime will refuse to load without them
- `integrates_with` — optional packs that improve behavior; pack must work without them
- Never put a domain concept in a pack just because it's convenient — it belongs in the most specific pack that truly owns it
- Do not duplicate object types across packs — use relations to connect objects from different packs
- Core Pack objects (source, observation, task, action, artifact, memory_candidate, evaluation) are the universal lingua franca — domain packs should map their outputs to Core types

### 6. Open-Source Hygiene Checklist

Before submitting any pack:
- [ ] `README.md` exists with behavior map, object types table, relation types table, dependency declarations, and 2+ usage examples
- [ ] `CHANGELOG.md` exists starting at v0.1.0
- [ ] `fixtures/` contains at least one scenario fixture
- [ ] All behaviors have `description` strings
- [ ] All object types have `description` strings in `ObjectType(...)`
- [ ] `settings.py` — all fields have defaults (no required fields without defaults)
- [ ] Pack passes `activegraph quickstart` fixture run (or has its own fixture runner)
- [ ] No secrets, credentials, or API keys are hardcoded anywhere
- [ ] `pyproject.toml` entry point is registered for the pack

### 7. Design Rules (No Turn Coordinator)

- Do **not** build a central coordinator or orchestration manager — coordination is emergent from graph-visible behavior outputs
- Do **not** let chat own the assistant loop — communication is just one channel
- Do **not** put context assembly in a global blob — each behavior gets a behavior-specific view
- Do **not** refactor existing packs (e.g., Diligence) — add bridges instead
- Do **not** add `context_requirement` objects yet — use behavior view specs instead
- Do **not** put person/company/claim/evidence in Core — those belong in Entity/domain packs

## Architecture Decisions

- **Core Pack is minimal by design** — only 7 universal primitives; everything else is domain-specific. Prevents Core from becoming a universal ontology.
- **Packs compose through graph state, not function calls** — behaviors emit events that trigger other behaviors; no direct inter-pack method calls.
- **Behavior maps are first-class documentation** — every pack README includes a Mermaid DAG of its behavior chain. This is the primary developer UX.
- **Bundles are presets, not primitives** — a bundle is just a list of packs with a factory function; no new ontology.
- **Fixtures are deterministic and API-key-free** — every pack ships with fixtures so the demo works without any credentials.

## Product

An open-source library of ActiveGraph packs that makes it easy for developers to build long-running, auditable assistants. Includes a modular inspector UI for visualizing the event trace, graph state, behavior maps, and pack capabilities in real time.

## User preferences

- Keep everything tidy and organized for open-source sharing
- Core rules must be enforced across all packs
- Use the activegraph PyPI package and follow its patterns
- Modular UI with widget system (global widgets + pack-specific widgets)
- Design thinking should be documented well

## Gotchas

- `activegraph` requires Python 3.11+ (tomllib is stdlib in 3.11)
- Always run `pip install -e ".[dev]"` after adding a new pack to register its entry point
- LLM behaviors need `deterministic=True` to work with fixtures (no API key needed)
- When adding a new pack, register it in `pyproject.toml` under `[project.entry-points."activegraph.packs"]`
- Core Pack object types use `snake_case` — do NOT use CamelCase for the `ObjectType.name` field

## Pointers

- Architecture doc: `docs/architecture.md` (direction report: `docs/activegraph-direction-report.md`)
- ActiveGraph docs: https://docs.activegraph.ai
- PyPI: https://pypi.org/project/activegraph/
- Diligence pack reference: see `activegraph/packs/diligence/` in the installed package
- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
