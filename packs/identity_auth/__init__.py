"""activegraph.packs.identity_auth — Identity/Auth Pack v0.1.

Answers "who is speaking, how confident are we, what can they do?"

Enables differentiated behavior for owner vs. external contact vs. unknown
principal. Every incoming source is resolved to a Principal with a role
and confidence score. An AuthContext scopes the session.

Object types:
  principal    — Recognized identity with role + confidence
  auth_context — Session-scoped authentication state
  role         — Named role with capability list
  permission   — Explicit action/resource grant
  delegation   — Temporary scope transfer between principals

Behaviors:
  principal_resolver   — source.created → creates principal
  auth_context_builder — principal.created → creates auth_context
  permission_checker   — action.created (proposed) → checks role, may reject

Relation types:
  resolves_to, authenticated_by, granted_by, granted_to, linked_to_entity

Composes with:
  - Core Pack (sources trigger principal_resolver)
  - Entity Pack (Principal.entity_id links to Entity)
  - Agent Profile Pack (audience_role from principal shapes context views)

Usage:
    from activegraph import Runtime, Graph
    from packs.core import pack as core_pack
    from packs.identity_auth import pack as identity_pack, IdentitySettings

    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    rt.load_pack(identity_pack, settings=IdentitySettings(
        owner_identifiers=["alice@example.com"],
        default_external_role="external",
    ))

    graph.add_object("source", {
        "kind": "chat_message",
        "content": "Hello",
        "sender_ref": "alice@example.com",
        "channel": "chat",
    })
    rt.run_until_idle()
    # principal with role="owner" is now in the graph
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import IdentitySettings
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# requires=["core"], composes_with=["entity"]
pack = Pack(
    name="identity_auth",
    version="0.2.1",
    description=(
        "Identity resolution and permission checking. "
        "Resolves every source to a Principal (owner/admin/collaborator/external/customer/unknown/blocked). "
        "Creates AuthContext per session. Blocks unsafe actions from low-trust principals."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=load_prompts_from_dir(_PROMPTS_DIR) if _PROMPTS_DIR.exists() else (),
    settings_schema=IdentitySettings,
)

__all__ = ["pack", "IdentitySettings"]
