"""Projects: proposed from evidence, promoted by owner verdict (ADR 0040).

The derivation is deterministic and explainable, in seed-priority order:
owner-confirmed facts, the owner's own connector taxonomy (e.g. Gmail user
labels), entities recurring in owner-engaged communication, and
presence/research entities. An LLM may describe a cluster via annotation;
it can never mint one. Confirm / rename / dismiss follow ADR 0020/0036
semantics. Routing items into projects is the next slice.
"""

from activegraph.packs import Pack

from .object_types import OBJECT_TYPES, RELATION_TYPES
from .tools import TOOLS

# requires=["subject_profile"], integrates_with=["entity", "communication", "attention", "tool_gateway"]
pack = Pack(
    name="projects",
    version="0.1.0",
    description=(
        "Evidence-derived project candidates with explainable sources and "
        "owner-verdict promotion; deterministic derivation, supersession "
        "lifecycles, and a neutral projection."
    ),
    object_types=tuple(OBJECT_TYPES),
    relation_types=tuple(RELATION_TYPES),
    behaviors=(),
    tools=tuple(TOOLS),
    policies=(),
    prompts=(),
)

__all__ = ["pack"]
