"""activegraph.packs.tool_gateway — Tool Gateway Pack v0.2.

All external capability calls (APIs, MCP, local tools, SDK clients) must
flow through this pack. It normalizes calls, runs policy checks, records
actions, and maps results back to Core source objects.

Key invariants:
  - Secrets never enter model context — use credential_ref_name (a name, not a value)
  - All calls are graph-visible: CapabilityCall and CapabilityResult are graph objects
  - Policy decisions are graph-visible: status field on CapabilityCall
  - Tool outputs become Core source objects → enabling downstream observation extraction

Object types: capability_provider, capability_call, capability_approval,
              capability_denial, capability_result
Behaviors:    call_recorder, policy_enforcer, call_executor, result_sourcer
Tools:        execute_capability, pending_approvals, approve_capability,
              deny_capability
LLM proxies:  llm_tools.as_llm_tool / llm_tools_for — registered capabilities
              as policy-checked Tools for @llm_behavior(tools=[...])

Usage:
    from activegraph import Runtime, Graph
    from packs.core import pack as core_pack
    from packs.tool_gateway import pack as tg_pack, ToolGatewaySettings

    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    rt.load_pack(tg_pack, settings=ToolGatewaySettings(
        auto_approve_risk_classes=["low", "medium"],
    ))
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import ToolGatewaySettings
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# requires=["core"], integrates_with=["secrets", "identity_auth"]
pack = Pack(
    name="tool_gateway",
    version="0.5.1",
    description=(
        "Capability execution gateway. All external calls (APIs, MCP, local tools) "
        "flow through here for policy checks, credential injection by reference, "
        "recording, output sanitization, injection scanning, and result mapping "
        "to Core source objects. Tool output reaches models fenced as untrusted "
        "external content."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=load_prompts_from_dir(_PROMPTS_DIR) if _PROMPTS_DIR.exists() else (),
    settings_schema=ToolGatewaySettings,
)

__all__ = ["pack", "ToolGatewaySettings"]
