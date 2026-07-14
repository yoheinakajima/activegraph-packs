# AGENTS.md — activegraph-packs

Working guide for coding agents. The docs in `activegraph-vision` (ADRs,
DECISIONS.md, GLOSSARY.md) are the cross-repo spec and win over any prompt;
genuine conflicts get flagged, never silently resolved in code.

## What this repo is

The official pack library for ActiveGraph: a **reusable capability library
with thin connector recipes** — memory, tools, identity, communication,
channels, scheduling, MCP, connectors — that assistants on the runtime
compose from, plus the conventions (layering, coordination, fixtures,
manifest format) that keep a multi-pack assistant coherent. 38 pack entry
points today (see `pyproject.toml`); `bundles/` + `packs/demo_server.py` are
a reference chassis, not the product.

**Boundary — what must NEVER live here:** product opinionation (sequencing,
onboarding flows, copy, companion, ranking, UX) — that is
`babyagi-activegraph`; runtime law (event sourcing, replay, authority
enforcement) — that is `activegraph`. Packs expose neutral facts and
governed capabilities.

## Composition rules that bite

- Packs coordinate through graph state and events: one pack writes an object,
  the write is an event, the event fires another pack's behavior. **No direct
  cross-pack function calls** except declared stable APIs (e.g. tool functions
  that hosts/bundles import); no central orchestrator.
- One global ObjectType name = exactly one owner pack (the `projects` pack
  owns `project`). Other packs contribute candidates/annotations through the
  owner's pipeline, never a second canonical store.
- Capability surface is declared THREE ways that must agree:
  `register_local_capability(...)` call kwargs, `Pack(capabilities=
  (CapabilityDecl(...),))` in `__init__.py`, and `manifest.toml`
  `[[surface.capabilities]]` (provider/capability/risk_class/action_class).
  `tests/test_manifests.py` AST-checks them.
- Every pack change bumps that pack's version and its `CHANGELOG.md`.
- Every pack ships deterministic fixtures (`fixtures/run_fixtures.py`, no
  LLM/API key) that CI runs individually.

## Setup

- Shared venv: `/Users/yoheinakajima/code/runtime-and-packs/.venv`
  (Python 3.11). This repo and `babyagi-activegraph` are installed editable
  (`.venv/bin/pip install -e ".[dev]"`); the `activegraph` runtime dependency
  (`activegraph[llm]>=1.9,<2.0`) comes from PyPI (1.10.0). If you clone the
  runtime for source work, name the directory `activegraph-src` — a directory
  named `activegraph` shadows the installed package.
- Git pushes work over SSH remotes, not HTTPS; no `gh` CLI.

## Gates (run from this repo root; all must pass)

```bash
/Users/yoheinakajima/code/runtime-and-packs/.venv/bin/python -m pytest tests/ -q
/Users/yoheinakajima/code/runtime-and-packs/.venv/bin/python -m packs.doctor
/Users/yoheinakajima/code/runtime-and-packs/.venv/bin/python packs/<pack>/fixtures/run_fixtures.py   # your pack
/Users/yoheinakajima/code/runtime-and-packs/.venv/bin/python scripts/generate_manifests.py           # then: git diff must be clean
```

CI (`.github/workflows/ci.yml`) regenerates all manifests and fails on any
diff (drift gate), runs every per-pack fixture suite, the cross-pack
integration suites under `packs/fixtures/`, then the full pytest suite.

## Canonical paths

- `packs/<name>/` — one pack: `__init__.py` (exports `pack` + settings),
  `object_types.py`, `behaviors.py`, `tools.py`, `settings.py` (all defaults),
  `prompts/`, `fixtures/`, `README.md`, `CHANGELOG.md`, `manifest.toml`
  (generated). Importer format adapters live under `packs/importers/<name>/`.
- New pack: copy `packs/_template`, add an entry point under
  `[project.entry-points."activegraph.packs"]` in `pyproject.toml` AND the
  `PACK_PATHS` map in `scripts/generate_manifests.py`, reinstall editable.
- `bundles/` — composed runtimes (assistant, messaging, email, research)
- `tests/` — pytest suites; `packs/fixtures/` — cross-pack integration runners
- `docs/manifest-spec.md`, `docs/concepts.md`; `CONTRIBUTING.md` is the
  canonical pack-author guide; `lib/` + pnpm workspaces — React Inspector UI

## Definition of done

1. Pack fixture suite green; cross-pack suites green; full pytest green;
   `python -m packs.doctor` exits 0.
2. Manifests regenerated and committed clean (drift gate passes).
3. Pack version + CHANGELOG.md bumped; README behavior map current.
4. Capability declarations agree in all three places; no undeclared
   cross-pack calls; object-type ownership respected.
