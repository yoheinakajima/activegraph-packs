# ActiveGraph Bundles

Bundles are pre-assembled collections of packs for common assistant configurations.

## What is a Bundle?

A bundle is **not a pack**. It has no new object types, behaviors, or ontology.
It is simply:

1. A **named list** of packs in the recommended load order
2. A **factory function** that creates a configured Runtime with all packs loaded
3. **Sensible defaults** for settings appropriate to the bundle's use case

Use a bundle when you want a working assistant quickly without manually loading
each pack, configuring settings, and ensuring the right load order.

## Available Bundles

### Assistant Bundle (`bundles/assistant.py`)

The base bundle for any interactive assistant. Provides the full infrastructure
stack: identity resolution, memory, tool execution, communication, and chat.

**Packs:** `core` → `tool_gateway` → `secrets` → `memory_gateway` → `agent_profile` → `identity_auth` → `communication` → `chat`

```python
from bundles.assistant import build_assistant
from packs.identity_auth import IdentitySettings

rt = build_assistant(
    identity_settings=IdentitySettings(owner_identifiers=["alice@example.com"]),
)
rt.run_goal("Help me with...")
```

### Email Assistant Bundle (`bundles/email_assistant.py`)

Adds email processing, entity tracking, and CRM capabilities.

**Packs:** *Assistant Bundle* + `entity` + `email`

```python
from bundles.email_assistant import build_email_assistant
from packs.email import EmailSettings

rt = build_email_assistant(
    email_settings=EmailSettings(
        owner_email_addresses=["alice@example.com"],
        require_approval_for_external=True,
    ),
)
```

### Research Bundle (`bundles/research_bundle.py`)

Research assistant for paper processing and hypothesis generation.
Headless-friendly — intentionally excludes `agent_profile`, `identity_auth`, and `secrets`.

**Packs:** `core` + `tool_gateway` + `memory_gateway` + `communication` + `chat` + `research`

```python
from bundles.research_bundle import build_research_assistant
from packs.research import ResearchSettings

rt = build_research_assistant(
    research_settings=ResearchSettings(min_coherence_for_hypothesis=0.5),
)
```

## Customizing a Bundle

Bundles are **starting points**. Add packs on top, or build a custom factory:

```python
from bundles.assistant import build_assistant
from packs.codebase import pack as codebase_pack, CodebaseSettings
from packs.team_ops import pack as team_ops_pack, TeamOpsSettings

# Method 1: Start from a bundle and add packs
rt = build_assistant()
rt.load_pack(codebase_pack, settings=CodebaseSettings(auto_create_issues_as_tasks=True))
rt.load_pack(team_ops_pack, settings=TeamOpsSettings())

# Method 2: Build a custom factory for your product
def build_engineering_assistant(**kwargs):
    rt = build_assistant(**kwargs)
    rt.load_pack(codebase_pack, settings=CodebaseSettings())
    rt.load_pack(team_ops_pack, settings=TeamOpsSettings())
    return rt
```

## When NOT to Use Bundles

- **Focused tools (2–3 packs)**: Load packs directly for less overhead
- **Fine-grained settings control**: Use explicit `rt.load_pack()` calls per pack
- **Custom pack order**: Bundles use opinionated load order; override if yours differs
- **Headless research pipelines**: Load just `core` + `research` if you don't need chat

## Diligence-Core Bridge

`diligence_core_bridge` is a zero-ontology pack (no new object types or relation types)
that subscribes to Diligence event types and creates parallel Core primitives:

| Diligence type | Core equivalent | Key mapping |
|---|---|---|
| `document` | `source` (kind=document) | summary→content, url→url |
| `claim` | `observation` | text→text, confidence→confidence |
| `memo` | `artifact` (kind=memo) | summary+claims→content |
| `risk` | `evaluation` | severity→judgment, description→rationale |

All Core objects carry a `derived_from` relation back to their Diligence origin.
The bridge never modifies Diligence objects.

## Bundle Compatibility Matrix

| Pack | Assistant | Email Asst | VC | Research |
|------|:---------:|:----------:|:--:|:--------:|
| `core` | ✅ | ✅ | ✅ | ✅ |
| `tool_gateway` | ✅ | ✅ | ✅ | ✅ |
| `secrets` | ✅ | ✅ | ✅ | ❌ |
| `memory_gateway` | ✅ | ✅ | ✅ | ✅ |
| `agent_profile` | ✅ | ✅ | ✅ | ❌ |
| `identity_auth` | ✅ | ✅ | ✅ | ❌ |
| `communication` | ✅ | ✅ | ✅ | ✅ |
| `chat` | ✅ | ✅ | ✅ | ✅ |
| `email` | ❌ | ✅ | ✅ | ❌ |
| `entity` | ❌ | ✅ | ✅ | ❌ |
| `diligence`* | ❌ | ❌ | ⚠️* | ❌ |
| `diligence_core_bridge` | ❌ | ❌ | ✅ | ❌ |
| `meeting` | ❌ | ❌ | ✅ | ❌ |
| `research` | ❌ | ❌ | ❌ | ✅ |
| `codebase` | add-on | add-on | add-on | ❌ |
| `team_ops` | add-on | add-on | add-on | ❌ |

*⚠️ Diligence v1.0.5 declares `derived_from` which conflicts with Core Pack.
The `diligence_core_bridge` works without Diligence co-loaded — it subscribes by
object type name, so injected Diligence-type objects still map to Core primitives.

## Examples

See `bundles/examples/` for runnable demos:

| File | Bundle | What it shows |
|---|---|---|
| `assistant_example.py` | Assistant | Chat input → CommMessage → Principal → ChatTurn |
| `email_assistant_example.py` | Email Assistant | Inbound email → EmailThread + CommIntent + entity |
| `vc_example.py` | VC | Founder email + diligence objects + meeting transcript |
| `research_example.py` | Research | Two papers → idea atoms → hypothesis → experiment |
