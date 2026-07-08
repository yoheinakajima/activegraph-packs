# Bridge Packs — v0.1

Bridge packs connect existing domain packs to the Core primitive layer **without modifying the source pack**. They subscribe to source-pack `object.created` events and emit Core equivalents with `derived_from` relations.

---

## What bridges are for

ActiveGraph packs coordinate through shared graph state. When a domain pack (like Diligence) creates objects (`document`, `claim`, `memo`, `risk`), other packs that speak only Core primitives (`source`, `observation`, `artifact`, `evaluation`) can't see them.

A bridge pack solves this without touching either side:

```
Diligence pack           Bridge pack              Core-aware packs
document.created  ──▶  document_to_source  ──▶  source(kind=document)
claim.created     ──▶  claim_to_observation──▶  observation(category=fact)
memo.created      ──▶  memo_to_artifact    ──▶  artifact(kind=memo)
risk.created      ──▶  risk_to_evaluation  ──▶  evaluation(judgment=severity)
```

Every bridged object carries a `derived_from(core_obj → source_obj)` relation so the provenance chain is always visible in the graph.

---

## Available bridges

### `diligence_core_bridge`

Maps [Diligence pack](https://pypi.org/project/activegraph/) objects to Core primitives.

| Diligence object | Core object | Behavior |
|---|---|---|
| `document` | `source` (kind=document) | `document_to_source` |
| `claim` | `observation` (category=fact) | `claim_to_observation` |
| `memo` | `artifact` (kind=memo, format=markdown) | `memo_to_artifact` |
| `risk` | `evaluation` (judgment=severity) | `risk_to_evaluation` |

#### What gets mapped

**document → source**
- `source.content` = `document.summary`
- `source.url` = `document.url`
- `source.metadata` carries `title`, `company_id`, `published_at`, `diligence_document_id`

**claim → observation**
- `observation.text` = `claim.text`
- `observation.confidence` = `claim.confidence`
- `observation.category` = `"fact"`
- If the parent document was already bridged, a `grounds(source → observation)` relation is also created

**memo → artifact**
- `artifact.content` is assembled Markdown: summary, key claims, thesis questions, open contradictions, risks
- `artifact.kind` = `"memo"`, `artifact.format` = `"markdown"`, `artifact.status` = `"draft"`

**risk → evaluation**
- `evaluation.judgment` = `risk.severity`
- `evaluation.rationale` = `risk.title + ": " + risk.description`
- `evaluation.evaluator` = `"diligence_core_bridge"`

#### Usage

```python
from activegraph import Graph, Runtime
from activegraph.packs import load_by_name
from packs.bridges import diligence_core_bridge

# Load order matters: source pack first, bridge second
rt = Runtime(Graph())
rt.load_pack(load_by_name("diligence"))
rt.load_pack(diligence_core_bridge)

# Diligence objects are now bridged to Core automatically
rt.run_goal("Diligence: Northwind Robotics")
# → Each document, claim, memo, and risk also appears as a Core primitive
```

#### The `derived_from` conflict

Both Core Pack and the Diligence pack declare a `derived_from` relation type. Loading them both without a shim causes a `PackConflictError`. Strip the Diligence pack's copy before loading it:

```python
from activegraph.packs import load_by_name
from activegraph import Pack

diligence = load_by_name("diligence")
compat_diligence = Pack(
    **{k: v for k, v in diligence.__dict__.items() if k != "relation_types"},
    relation_types=tuple(
        r for r in diligence.relation_types if r.name != "derived_from"
    ),
)
rt.load_pack(compat_diligence)
rt.load_pack(diligence_core_bridge)
```

---

## Writing a new bridge

To bridge another domain pack's objects to Core (or to each other):

### 1. Create the bridge module

```python
# packs/bridges/my_pack_core.py
from activegraph.packs import behavior

_BRIDGE_SEEN: dict[str, str] = {}

def clear_bridge_registry() -> None:
    _BRIDGE_SEEN.clear()

@behavior(
    name="my_object_to_source",
    on=["object.created"],
    where={"object.type": "my_object"},
    creates=["source"],
    description="Maps my_object to a Core source with derived_from relation.",
)
def my_object_to_source(event, graph, ctx, *, settings=None):
    obj = event.payload.get("object", {})
    obj_id = obj.get("id")
    if not obj_id or obj_id in _BRIDGE_SEEN:
        return

    data = obj.get("data", {})
    try:
        src = graph.add_object("source", {
            "kind": "my_object",
            "content": data.get("content", ""),
            "metadata": {"bridge": "my_pack_core", "original_id": obj_id},
        })
        _BRIDGE_SEEN[obj_id] = src.id
        graph.add_relation("derived_from", src.id, obj_id)
    except Exception:
        pass

BEHAVIORS = [my_object_to_source]
```

### 2. Register the bridge pack in `__init__.py`

```python
# packs/bridges/__init__.py  (add to existing file)
from .my_pack_core import BEHAVIORS as MY_PACK_CORE_BEHAVIORS

my_pack_core_bridge = Pack(
    name="my_pack_core_bridge",
    version="0.1.0",
    description="Maps my_pack objects to Core primitives.",
    object_types=[],
    relation_types=[],
    behaviors=MY_PACK_CORE_BEHAVIORS,
    ...
)
```

### 3. Register in `pyproject.toml`

```toml
[project.entry-points."activegraph.packs"]
my_pack_core_bridge = "packs.bridges:my_pack_core_bridge"
```

### 4. Load after the source pack

```python
rt.load_pack(my_pack)
rt.load_pack(my_pack_core_bridge)
```

---

## Bridge rules

- **Non-destructive** — bridges never modify source-pack objects
- **Deduplication** — use a `_BRIDGE_SEEN` registry to prevent double-bridging on event replay
- **`derived_from` relations** — every bridged object must carry a `derived_from(core_obj → source_obj)` relation for provenance
- **Load order** — source pack must be loaded before the bridge
- **Fail gracefully** — all `graph.add_object` and `graph.add_relation` calls are wrapped in `try/except`
