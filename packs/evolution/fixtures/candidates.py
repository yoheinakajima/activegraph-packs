"""The scripted author for evolution fixtures (design §8: fixtures use a
scripted generator, never a live LLM).

`author_pack` produces a gate-compliant candidate pack as a file dict,
with the manifest's content hash computed by the runtime's reference
implementation over the exact files, so hash gates exercise real
machinery, never fixture-crafted strings. Variants twist one knob each
for the gate-matrix scenarios.
"""

from __future__ import annotations

from packs.evolution.materialize import write_files

BEHAVIOR_TEMPLATE = '''"""Behaviors for the {name} candidate pack."""

from __future__ import annotations

from activegraph.packs import behavior

from .settings import GreeterSettings


@behavior(
    name="greeter",
    on=["object.created"],
    where={{"object.type": "source"}},
    creates=["{log_type}"],
)
def greeter(event, graph, ctx, *, settings: GreeterSettings):
    """Log a greeting for every source the assistant sees."""
    obj = event.payload.get("object", {{}})
    data = obj.get("data", {{}})
{body}
    graph.add_object("{log_type}", {{
        "note": f"greeted source {{obj.get('id', '')}}",
        "prefix": settings.prefix,
    }})


@behavior(
    name="config_toucher",
    on=["object.created"],
    where={{"object.type": "chat_input"}},
)
def config_toucher(event, graph, ctx, *, settings: GreeterSettings):
    """Patch the shared greeter_config counter (conflict-fixture surface)."""
    obj = event.payload.get("object", {{}})
    content = str((obj.get("data") or {{}}).get("content", ""))
{trigger}
    for cfg in ctx.view.objects(type="greeter_config"):
        seen = int((cfg.data or {{}}).get("seen", 0))
        graph.patch_object(cfg.id, {{"seen": seen + 1}})
        break
    graph.add_object("{log_type}", {{"note": f"heard: {{content[:40]}}",
                                       "prefix": settings.prefix}})


BEHAVIORS = [greeter, config_toucher]
'''

OBJECT_TYPES_TEMPLATE = '''"""Object types for the candidate pack."""

from __future__ import annotations

from pydantic import BaseModel, Field

from activegraph.packs import ObjectType


class GreetingLog(BaseModel):
    note: str = Field(default="")
    prefix: str = Field(default="")


OBJECT_TYPES = [
    ObjectType(name="{log_type}", schema=GreetingLog,
               description="A logged greeting."),
]
RELATION_TYPES = []
'''

SETTINGS_SRC = '''"""Settings for the candidate pack."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GreeterSettings(BaseModel):
    prefix: str = Field(default="hello")
'''

TOOLS_SRC = '''"""No tools in this candidate."""

TOOLS = []
'''

INIT_TEMPLATE = '''"""{name}: an agent-authored candidate pack."""

from __future__ import annotations

from activegraph.packs import Pack

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import GreeterSettings

pack = Pack(
    name="{name}",
    version="0.1.0",
    description="Logs greetings for sources (fixture candidate).",
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=(),
    policies=(),
    prompts=(),
    settings_schema=GreeterSettings,
)
'''

FIXTURES_TEMPLATE = '''"""Self-contained fixtures for the candidate pack."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from activegraph import Graph, Runtime

from {name} import pack


def run_all() -> bool:
    rt = Runtime(Graph())
    rt.load_pack(pack)
    rt.graph.add_object("source", {{"kind": "note", "content": "hi"}})
    rt.run_until_idle()
    logs = list(rt.graph.objects(type="{log_type}"))
    assert logs, "greeter must log a greeting for a source"
    print("ALL PASS")
    return True


def main(rt):
    """Trial-child entrypoint (chassis contract): raise on failure.
    These fixtures are self-contained; the fork runtime is unused."""
    assert run_all()


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
'''

MANIFEST_TEMPLATE = '''[pack]
name = "{name}"
version = "0.1.0"
description = "Logs greetings for sources (fixture candidate)."
license = "Apache-2.0"

[pack.provenance]
authors = ["scripted author"]
authored_by = "agent"
generator = "packs/evolution/fixtures/candidates.py"
source_url = ""
created_at = "2026-07-08T00:00:00Z"

[pack.integrity]
content_hash = "{content_hash}"

[dependencies]
activegraph = ">=1.4,<2.0"
python = ">=3.11"
python-deps = []

[surface]
object_types = ["{log_type}"]
relation_types = []
behaviors = {behaviors}
tools = []
settings_schema = "GreeterSettings"
{capabilities}
[fixtures]
entrypoint = "fixtures/run_fixtures.py"
deterministic = true
'''


def author_pack(
    name: str = "greeter_pack",
    *,
    log_type: str = "greeting_log",
    behavior_body: str = "    pass",
    trigger: str = "    pass",
    extra_behavior_src: str = "",
    undeclared_extra: bool = False,
    banned_import: bool = False,
    banned_construct: bool = False,
    reserved_capability: bool = False,
    oversize: bool = False,
    break_content_hash: bool = False,
    hang_on_import: bool = False,
    extra_module_src: str = "",
    charter_path_file: bool = False,
) -> dict[str, str]:
    """Produce the candidate's file dict; each flag twists one gate.

    `hang_on_import` passes every static gate and then spins forever at
    module import: the fixture-3 subprocess twin. In the in-process era
    this would have hung the parent runtime at trial materialization;
    under the v1.5 sandbox the child gets killed at the wall clock and
    the parent records the rejection."""
    behaviors_src = BEHAVIOR_TEMPLATE.format(
        name=name, body=behavior_body, trigger=trigger, log_type=log_type)
    if banned_import:
        behaviors_src = "import os\n" + behaviors_src
    if banned_construct:
        behaviors_src += "\n_x = eval('1 + 1')\n"
    if extra_behavior_src:
        behaviors_src += extra_behavior_src
    if hang_on_import:
        behaviors_src += "\nwhile True:\n    pass\n"
    if extra_module_src:
        # Raw module-level source, no declaration changes (the soak
        # harness's resource-hog candidates use this).
        behaviors_src += extra_module_src

    declared_behaviors = '["greeter", "config_toucher"]'
    if extra_behavior_src and not undeclared_extra:
        declared_behaviors = '["greeter", "config_toucher", "extra"]'

    capabilities_block = ""
    if reserved_capability:
        capabilities_block = (
            '\n[[surface.capabilities]]\n'
            'provider = "helper"\n'
            'capability = "approve_capability"\n'
            'risk_class = "low"\n'
            'credential_ref = ""\n\n'
        )

    settings_src = SETTINGS_SRC
    if oversize:
        settings_src += "\n# " + ("x" * 30_000) + "\n"

    from packs.evolution.trial_driver import (
        TRIAL_DRIVER_PATH,
        render_trial_driver,
    )

    files = {
        "__init__.py": INIT_TEMPLATE.format(name=name),
        "object_types.py": OBJECT_TYPES_TEMPLATE.format(log_type=log_type),
        "behaviors.py": behaviors_src,
        "settings.py": settings_src,
        "tools.py": TOOLS_SRC,
        "fixtures/run_fixtures.py": FIXTURES_TEMPLATE.format(name=name, log_type=log_type),
        # The chassis driver, included verbatim (gate 0b verifies bytes).
        TRIAL_DRIVER_PATH: render_trial_driver(),
    }

    if reserved_capability:
        # The registration call, sans import: gate 3 sees declared==actual,
        # gate 6 must be the one that rejects (never-LLM-callable name).
        files["tools.py"] += (
            '\n\ndef _wire():\n'
            '    register_local_capability("helper", "approve_capability",\n'
            '                              lambda: None)\n'
        )

    # The manifest's content hash: computed by the RUNTIME over the exact
    # files (sans manifest), so the hash gates exercise real machinery.
    from activegraph.packs.manifest import compute_content_hash
    root = write_files(files, pack_name=name)
    content_hash = compute_content_hash(root)
    if break_content_hash:
        content_hash = "sha256:" + "0" * 64

    files["manifest.toml"] = MANIFEST_TEMPLATE.format(
        name=name, content_hash=content_hash, log_type=log_type,
        behaviors=declared_behaviors, capabilities=capabilities_block)

    if charter_path_file:
        # An authored file targeting the reserved charter path: refused
        # at the charter gate before any other check (llm-author-design
        # §3a/§8). Added after the hash so the gate, not the hash, is
        # what stops it.
        from packs.evolution.author_frame import AUTHOR_CHARTER_FILENAME
        files[AUTHOR_CHARTER_FILENAME] = "# a charter the author must not write\n"
    return files
