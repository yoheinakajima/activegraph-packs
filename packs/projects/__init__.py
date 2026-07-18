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
from .router import (
    bootstrap_associations_fn,
    derive_route_fn,
    route_pending_fn,
    unrouted_items_fn,
)
from .tools import (
    TOOLS,
    archive_project_fn,
    create_workstream_fn,
    describe_project_fn,
)

# requires=["subject_profile"], integrates_with=["entity", "communication", "attention", "tool_gateway"]
pack = Pack(
    name="projects",
    version="0.5.0",
    description=(
        "Evidence-derived project candidates with explainable sources and "
        "owner-verdict promotion; deterministic derivation, supersession "
        "lifecycles, owner-authored workstreams, the evidence router with "
        "honest unfiled state, and a neutral projection."
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
    "archive_project_fn",
    "associate_workstream_fn",
    "bootstrap_associations_fn",
    "correct_routing_fn",
    "create_workstream_fn",
    "derive_route_fn",
    "descendants_fn",
    "describe_project_fn",
    "link_workstreams_fn",
    "project_context_packet_fn",
    "project_organizational_views_fn",
    "promoted_view_fn",
    "propose_organizational_view_fn",
    "review_organizational_view_fn",
    "route_item_fn",
    "route_pending_fn",
    "unlink_workstreams_fn",
    "unrouted_items_fn",
]
