"""activegraph.packs.mcp — MCP Pack v0.1 (bidirectional).

Outbound — the assistant consumes MCP servers:
  registry.connect_and_register discovers a server's tools and registers
  each as a Tool Gateway capability (key: mcp_<server>.<tool>), so the
  entire MCP ecosystem arrives PRE-GOVERNED: recorded, policy-checked
  (high-risk/approval-required by default), sanitized, injection-scanned,
  and fenced as EXTERNAL CONTENT before it reaches the model.
  client.py speaks the protocol with stdlib only (stdio + streamable HTTP).

Inbound — other agents consume the assistant over MCP:
  server.MCPGateway exposes chat (the full pipeline, caller-identified),
  memory_search (subject-scoped to the caller), and selected gateway
  capabilities. Bearer tokens resolve to Identity/Auth principals; graph-
  native mcp_exposure rules decide which ROLE sees which surface
  (fail-closed; defaults: owner everything, others nothing); every call —
  allowed or refused — is an mcp_access audit object.

The exposure config is itself agent-editable through the governed
mcp.set_exposure capability (high risk → owner approval), so the
assistant can PROPOSE changes to its own MCP surface but never enact them
alone.

Object types: mcp_server, mcp_exposure, mcp_access
Behaviors:    none (network lives at the edge: host startup + HTTP mount)
Tools:        none (capabilities register through the Tool Gateway)
Driver:       demo server mounts POST /mcp (see packs/demo_server.py)

Entry point: registered as 'mcp' in [project.entry-points."activegraph.packs"]
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir
from activegraph.packs.manifest import CapabilityDecl

from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import MCPSettings

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# requires=["core", "tool_gateway"],
# integrates_with=["identity_auth", "chat", "memory_gateway", "secrets"]
pack = Pack(
    name="mcp",
    version="0.3.0",
    description=(
        "Bidirectional MCP: outbound servers' tools become governed Tool "
        "Gateway capabilities (approval-required by default); inbound, the "
        "assistant is itself an MCP server exposing chat, subject-scoped "
        "memory search, and selected capabilities — token-authenticated, "
        "role-gated by graph-native exposure rules, fully audited."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=(),
    tools=(),
    policies=(),
    prompts=load_prompts_from_dir(_PROMPTS_DIR) if _PROMPTS_DIR.exists() else (),
    # Declarative capability surface (Q8 mechanism chain, step 1):
    # mirrors this pack's register_local_capability host wiring so the
    # loader's two-way surface check covers capabilities too. CI's AST
    # check (tests/test_manifests.py) keeps this honest against the code.
    capabilities=(
        CapabilityDecl(provider='mcp', capability='set_exposure', risk_class='high', credential_ref='', action_class='R4'),
    ),
    settings_schema=MCPSettings,
)

__all__ = ["pack", "MCPSettings"]
