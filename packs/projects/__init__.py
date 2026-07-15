"""Projects: proposed from evidence, promoted by owner verdict (ADR 0040).

The derivation is deterministic and explainable, in seed-priority order:
owner-confirmed facts, the owner's own connector taxonomy (e.g. Gmail user
labels), entities recurring in owner-engaged communication, and
presence/research entities. An LLM may describe a cluster via annotation;
it can never mint one. Confirm / rename / dismiss follow ADR 0020/0036
semantics. Routing items into projects is the next slice.
"""

from activegraph.packs import Pack

from .graph import (
    associate_workstream_fn,
    correct_routing_fn,
    descendants_fn,
    link_workstreams_fn,
    project_context_packet_fn,
    project_organizational_views_fn,
    promoted_view_fn,
    propose_organizational_view_fn,
    review_organizational_view_fn,
    route_item_fn,
    unlink_workstreams_fn,
)
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .tools import TOOLS

# requires=["subject_profile"], integrates_with=["entity", "communication", "attention", "tool_gateway"]
pack = Pack(
    name="projects",
    version="0.4.0",
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

__all__ = [
    "pack",
    "associate_workstream_fn",
    "correct_routing_fn",
    "descendants_fn",
    "link_workstreams_fn",
    "project_context_packet_fn",
    "project_organizational_views_fn",
    "promoted_view_fn",
    "propose_organizational_view_fn",
    "review_organizational_view_fn",
    "route_item_fn",
    "unlink_workstreams_fn",
]
