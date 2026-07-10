"""Inbound MCP — the assistant as an MCP server.

Another agent (a Claude session, an orchestrator, a sibling assistant)
connects over MCP and, depending on who it is, can:

  * ``chat``            — talk to the assistant. The message enters the REAL
    chat pipeline with the caller's identity, so reply gating, memory,
    persona shaping, and the agentic tool loop all apply — an MCP caller is
    governed exactly like a Telegram sender.
  * ``memory_search``   — query the assistant's memory, subject-scoped to
    the caller (one agent never reads another user's memories).
  * exposed capabilities — invoke selected gateway capabilities. Calls run the
    SAME governed path as the assistant's own tool use: recorded,
    policy-checked, held for approval when risky.

Three layers decide what a caller can do:

  1. **Auth** — a bearer token maps to a caller identifier
     (``MCPSettings.tokens``); the identifier resolves to an Identity/Auth
     principal whose ROLE is the unit of exposure. Unknown token → refused.
     No token → anonymous (role None → nothing, under default exposure).
  2. **Exposure** — ``mcp_exposure`` graph objects say which surface each
     role may see. Fail-closed: no enabled rule, no access. Defaults are
     owner-everything / others-nothing. Rules are edited through the
     governed ``mcp.set_exposure`` capability (high risk → owner approval),
     which is also how the ASSISTANT can propose changes to its own MCP
     surface — config edits are capability calls, audited like any action.
  3. **Gateway policy** — exposed capabilities keep their risk classes; an
     inbound caller invoking a high-risk capability gets ``held_for_approval``,
     not execution.

Every inbound call — allowed or refused — is an ``mcp_access`` audit object.

Transport-wise this module is pure: ``MCPGateway.handle_jsonrpc`` takes a
parsed JSON-RPC message + token and returns a response dict (or None for
notifications). The demo server mounts it at POST /mcp; tests drive it
directly. Runtime work (chat, memory, capability execution) is injected as
callables so the gateway composes with any host.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .settings import MCPSettings

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "activegraph-assistant", "version": "0.2"}

# JSON-RPC error codes
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_UNAUTHORIZED = -32001
_FORBIDDEN = -32003


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- auth


def resolve_caller(graph, token: Optional[str], settings: MCPSettings) -> Optional[dict]:
    """Map a bearer token to {'identifier', 'role', 'verification'} or None.

    A valid configured token yields the caller identifier; the identifier's
    ROLE comes from Identity/Auth when principals are registered. Without
    principals, ``unverified_token_role`` applies (single-user mode) — the
    same "verification happens when verification is possible" rule the
    gateway's approver check uses, except that an INVALID or missing token
    is always anonymous: possession of a configured token is the floor.
    """
    if not token or token not in settings.tokens:
        return None
    identifier = settings.tokens[token]

    try:
        from packs.identity_auth.behaviors import principals_registered
        from packs.identity_auth.tools import lookup_principal_fn
        has_principals = principals_registered()
    except Exception:
        has_principals = False

    if not has_principals:
        role = settings.unverified_token_role or None
        if role is None:
            return None
        return {"identifier": identifier, "role": role,
                "verification": "identity_unverified"}

    principal = lookup_principal_fn(graph, identifier)
    if principal is None:
        return None  # identity system active + unknown identifier → refuse
    return {"identifier": identifier, "role": principal.get("role", "unknown"),
            "verification": "identity_verified"}


# ---------------------------------------------------------------- exposure


def ensure_default_exposures(graph, settings: MCPSettings) -> None:
    """Seed the default exposure rules once (idempotent).

    After seeding, the GRAPH is the source of truth — restarts must not
    resurrect defaults over operator edits, so seeding skips any surface
    that already has a rule object (enabled or not).
    """
    existing = set()
    try:
        for obj in graph.objects(type="mcp_exposure"):
            existing.add(obj.data.get("surface"))
    except Exception:
        pass
    for surface, roles in settings.default_exposures.items():
        if surface in existing:
            continue
        graph.add_object("mcp_exposure", {
            "surface": surface,
            "roles": list(roles),
            "enabled": True,
            "note": "default exposure (seeded at startup)",
            "updated_at": _now(),
            "updated_by": "mcp.defaults",
        })


def exposure_allows(graph, surface: str, role: Optional[str]) -> bool:
    """Does any enabled exposure rule grant *surface* to *role*? Fail-closed.

    Per-tool surfaces check their specific rule first (``tool:<key>``); a
    specific rule — allow or deny — overrides the generic ``tools`` gate.
    """
    if role is None:
        return False
    rules = {}
    try:
        for obj in graph.objects(type="mcp_exposure"):
            # Iteration is insertion-ordered: the LATEST rule per surface
            # wins, so a governed edit supersedes older rules (including
            # append-instead-of-patch edits from behavior context).
            rules[obj.data.get("surface")] = obj.data
    except Exception:
        return False

    def _rule_allows(rule: Optional[dict]) -> Optional[bool]:
        if rule is None:
            return None
        if not rule.get("enabled", True):
            return False
        return role in (rule.get("roles") or [])

    if surface.startswith("tool:"):
        specific = _rule_allows(rules.get(surface))
        if specific is not None:
            return specific
        return _rule_allows(rules.get("tools")) or False
    return _rule_allows(rules.get(surface)) or False


def set_exposure_fn(graph, surface: str, roles: list[str], enabled: bool = True,
                    note: str = "", updated_by: str = "") -> dict:
    """Create or update one exposure rule. The executor behind the governed
    ``mcp.set_exposure`` capability — reaching here means policy approved.

    In a behavior-sandbox graph view type iteration may be unavailable; the
    edit then APPENDS a fresh rule object instead of patching. That is fine:
    readers (exposure_allows) take the LATEST rule per surface, so the newest
    decision always governs and the history stays in the graph."""
    existing_id = None
    try:
        for obj in graph.objects(type="mcp_exposure"):
            if obj.data.get("surface") == surface:
                existing_id = obj.id  # keep scanning: LAST rule wins
    except Exception:
        pass
    data = {
        "surface": surface, "roles": list(roles), "enabled": enabled,
        "note": note, "updated_at": _now(), "updated_by": updated_by,
    }
    if existing_id:
        graph.patch_object(existing_id, data)
        return {"ok": True, "exposure_id": str(existing_id), "action": "updated"}
    obj = graph.add_object("mcp_exposure", data)
    return {"ok": True, "exposure_id": str(obj.id), "action": "created"}


def register_set_exposure_capability(*, risk_class: str = "high"):
    """Register ``mcp.set_exposure`` as a gateway capability.

    High risk by default → held for owner approval. Put it on the chat
    tool allow-list and the assistant can PROPOSE changes to its own MCP
    surface ("expose memory_search to agent role?") — the proposal is a
    capability_call, the decision is the owner's, the whole exchange is in
    the audit trail. This is the config-the-agent-can-edit seam.
    """
    from pydantic import BaseModel, Field
    from packs.tool_gateway.tools import register_local_capability

    class SetExposureInput(BaseModel):
        surface: str = Field(description=(
            "Surface to change: 'chat', 'memory_search', 'tools', or "
            "'tool:<capability_key>' for one tool."))
        roles: list[str] = Field(description=(
            "Principal roles allowed on this surface (e.g. ['owner', 'agent'])."))
        enabled: bool = Field(default=True)
        note: str = Field(default="", description="Why this change is needed.")

    def _executor(surface: str, roles: list, enabled: bool = True, note: str = "",
                  execution_context: Optional[dict] = None) -> dict:
        graph = (execution_context or {}).get("graph")
        if graph is None:
            raise RuntimeError("mcp.set_exposure requires gateway execution context")
        updated_by = (execution_context or {}).get("call_id", "gateway")
        return set_exposure_fn(graph, surface, list(roles), enabled, note,
                               updated_by=str(updated_by))

    return register_local_capability(
        "mcp", "set_exposure", _executor,
        input_schema=SetExposureInput,
        description=(
            "Change which MCP surface (chat / memory_search / tools / "
            "tool:<key>) is exposed to which principal roles. High-risk: "
            "held for owner approval."
        ),
        risk_class=risk_class,
        # R4: changing who may reach which surface is an authority
        # change — governance-shaped, never routine at any ceiling.
        action_class="R4",
    )


# ---------------------------------------------------------------- gateway


class MCPGateway:
    """Transport-agnostic inbound MCP endpoint over a live runtime graph.

    Host wiring (see the demo server):
      chat_fn(message, user_ref, session_id) -> {'content', 'session_id'}
      memory_fn(query, subject_ref, top_k)   -> list[result dicts]
    Capability exposure needs no wiring — it reads the gateway registry.
    """

    def __init__(
        self,
        graph_getter: Callable[[], Any],
        settings: MCPSettings,
        *,
        chat_fn: Optional[Callable[..., dict]] = None,
        memory_fn: Optional[Callable[..., list]] = None,
        gateway_settings=None,
    ):
        self._graph_getter = graph_getter
        self.settings = settings
        self._chat_fn = chat_fn
        self._memory_fn = memory_fn
        self._gw_settings = gateway_settings

    # -- audit ---------------------------------------------------------------

    def _record_access(self, graph, method: str, surface: str, caller: Optional[dict],
                       allowed: bool, reason: str) -> None:
        try:
            graph.add_object("mcp_access", {
                "method": method,
                "surface": surface,
                "caller": (caller or {}).get("identifier", ""),
                "role": (caller or {}).get("role"),
                "allowed": allowed,
                "reason": reason,
                "at": _now(),
            })
        except Exception:
            pass

    # -- tool catalog ----------------------------------------------------------

    def _visible_tools(self, graph, role: Optional[str]) -> list[dict]:
        tools: list[dict] = []
        if exposure_allows(graph, "chat", role) and self._chat_fn is not None:
            tools.append({
                "name": "chat",
                "description": (
                    "Send a message to this assistant and get its reply. The "
                    "full pipeline applies: identity, memory, persona, reply "
                    "gating, and its own governed tool use."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "The message."},
                        "session_id": {"type": "string",
                                       "description": "Continue an earlier session."},
                    },
                    "required": ["message"],
                },
            })
        if exposure_allows(graph, "memory_search", role) and self._memory_fn is not None:
            tools.append({
                "name": "memory_search",
                "description": (
                    "Search this assistant's long-term memory. Results are "
                    "scoped to YOUR identity — you see your memories and "
                    "shared/global ones, never another user's."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
            })
        if exposure_allows(graph, "catalog", role):
            tools.append({
                "name": "catalog_search",
                "description": (
                    "List the capabilities YOU can reach through this "
                    "assistant, with risk class, origin (native vs "
                    "MCP-derived), and whether a call will be held for "
                    "owner approval. Scoped to your role: capabilities "
                    "you cannot reach are not listed."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string",
                                  "description": "Substring filter (optional)."},
                    },
                },
            })
        from packs.tool_gateway.tools import get_capability_spec
        for key in self.settings.expose_capabilities:
            if not exposure_allows(graph, f"tool:{key}", role):
                continue
            spec = get_capability_spec(key)
            if spec is None or spec.input_schema is None:
                continue
            tools.append({
                "name": key.replace(".", "__"),  # MCP tool-name charset
                "description": f"{spec.description} [risk: {spec.risk_class}; "
                               "may be held for owner approval]",
                "inputSchema": spec.input_schema.model_json_schema(),
            })
        return tools

    def _caller_catalog(self, graph, caller: dict, query: str = "") -> list[dict]:
        """The catalog as seen by one inbound caller: only surfaces and
        capabilities their role can reach, annotated with governance
        metadata. Deliberately NOT the full registry — an inbound caller
        gets discovery over their own reach, never reconnaissance over
        everyone else's."""
        from packs.tool_gateway.gateway import decide_policy
        from packs.tool_gateway.settings import ToolGatewaySettings
        from packs.tool_gateway.tools import get_capability_spec

        role = caller.get("role")
        gw = self._gw_settings or ToolGatewaySettings()
        entries: list[dict] = []
        if exposure_allows(graph, "chat", role) and self._chat_fn is not None:
            entries.append({"key": "chat", "kind": "surface", "risk_class": "low",
                            "origin": "native", "held_on_call": False,
                            "description": "Converse with the assistant."})
        if exposure_allows(graph, "memory_search", role) and self._memory_fn is not None:
            entries.append({"key": "memory_search", "kind": "surface",
                            "risk_class": "low", "origin": "native",
                            "held_on_call": False,
                            "description": "Search your memories."})
        for key in self.settings.expose_capabilities:
            if not exposure_allows(graph, f"tool:{key}", role):
                continue
            spec = get_capability_spec(key)
            if spec is None:
                continue
            entries.append({
                "key": key,
                "kind": "capability",
                "risk_class": spec.risk_class,
                "origin": spec.origin,
                "held_on_call": decide_policy(spec.risk_class, gw) == "hold",
                "description": spec.description,
            })
        if query:
            q = query.lower()
            entries = [e for e in entries
                       if q in f"{e['key']} {e['description']}".lower()]
        entries.sort(key=lambda e: e["key"])
        return entries

    # -- governed capability call ----------------------------------------------

    def _call_capability(self, graph, key: str, arguments: dict, caller: dict) -> dict:
        """Run an exposed capability through the gateway: record → policy →
        execute-or-hold. Mirrors the LLM proxy flow with the MCP caller as
        the proposer, so inbound capabilities and the assistant's own tool use
        share one governance path."""
        from packs.tool_gateway.gateway import decide_policy, execute_approved_call
        from packs.tool_gateway.settings import ToolGatewaySettings
        from packs.tool_gateway.tools import get_capability_spec

        spec = get_capability_spec(key)
        if spec is None:
            return {"error": f"capability {key!r} is not registered"}
        gw = self._gw_settings or ToolGatewaySettings()
        decision = decide_policy(spec.risk_class, gw)

        call = graph.add_object("capability_call", {
            "provider_id": "",
            "provider_name": spec.provider_name,
            "capability_name": spec.capability_name,
            "input_data": arguments,
            "credential_ref_name": spec.credential_ref_name,
            "risk_class": spec.risk_class,
            "status": "approved" if decision == "auto_approve" else "policy_checking",
            "proposed_by": f"mcp:{caller['identifier']}",
            "proposed_at": _now(),
            "metadata": {"initiated_by": "mcp_inbound", "caller_role": caller.get("role")},
        })
        if decision == "hold":
            return {
                "status": "held_for_approval",
                "call_id": str(call.id),
                "note": (
                    f"This {spec.risk_class}-risk capability was recorded and "
                    "is waiting for the owner's approval."
                ),
            }
        result = execute_approved_call(
            graph, call.id, call.data, gw, executed_by=f"mcp:{caller['identifier']}",
        )
        graph.add_object("capability_approval", {
            "call_id": call.id,
            "provider_id": "",
            "provider_name": spec.provider_name,
            "capability_name": spec.capability_name,
            "input_data": arguments,
            "frame_id": None,
            "policy_decision": "auto_approved",
            "approver": "gateway_mcp_inbound",
            "approved_at": _now(),
            "metadata": {"risk_class": spec.risk_class, "executed": "inline"},
        })
        return {
            "status": result["status"],
            "call_id": str(call.id),
            "output": result.get("output_data", ""),
            "error": result.get("error"),
        }

    # -- JSON-RPC ----------------------------------------------------------------

    def handle_jsonrpc(self, message: dict, token: Optional[str]) -> Optional[dict]:
        """Handle one JSON-RPC message; returns the response (None for
        notifications). Auth and exposure failures are JSON-RPC errors AND
        mcp_access audit objects — refusals leave the same trail as grants."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return self._error(None, _INVALID_REQUEST, "not a JSON-RPC 2.0 message")

        method = message.get("method", "")
        msg_id = message.get("id")
        params = message.get("params") or {}
        graph = self._graph_getter()
        caller = resolve_caller(graph, token, self.settings)

        # Handshake is unauthenticated (a client must be able to introduce
        # itself), but carries no data and exposes nothing.
        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            })
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return self._result(msg_id, {})

        if method == "tools/list":
            if caller is None and token:
                self._record_access(graph, method, "", None, False, "invalid token")
                return self._error(msg_id, _UNAUTHORIZED, "invalid token")
            tools = self._visible_tools(graph, (caller or {}).get("role"))
            self._record_access(graph, method, "", caller, True,
                                f"{len(tools)} tools visible")
            return self._result(msg_id, {"tools": tools})

        if method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments") or {}
            if caller is None:
                reason = "invalid token" if token else "no token"
                self._record_access(graph, method, name, None, False, reason)
                return self._error(msg_id, _UNAUTHORIZED,
                                   f"authentication required ({reason})")
            return self._dispatch_call(graph, msg_id, name, arguments, caller)

        return self._error(msg_id, _METHOD_NOT_FOUND, f"method {method!r} not supported")

    def _dispatch_call(self, graph, msg_id, name: str, arguments: dict,
                       caller: dict) -> dict:
        role = caller.get("role")

        if name == "chat":
            if not exposure_allows(graph, "chat", role) or self._chat_fn is None:
                self._record_access(graph, "tools/call", "chat", caller, False,
                                    "chat not exposed to role")
                return self._error(msg_id, _FORBIDDEN, "chat is not exposed to you")
            self._record_access(graph, "tools/call", "chat", caller, True, "ok")
            reply = self._chat_fn(
                message=str(arguments.get("message", "")),
                user_ref=caller["identifier"],
                session_id=arguments.get("session_id"),
            )
            return self._tool_text(msg_id, reply.get("content", ""), {
                "session_id": reply.get("session_id"),
            })

        if name == "memory_search":
            if not exposure_allows(graph, "memory_search", role) or self._memory_fn is None:
                self._record_access(graph, "tools/call", "memory_search", caller,
                                    False, "memory_search not exposed to role")
                return self._error(msg_id, _FORBIDDEN, "memory_search is not exposed to you")
            self._record_access(graph, "tools/call", "memory_search", caller, True, "ok")
            results = self._memory_fn(
                query=str(arguments.get("query", "")),
                subject_ref=caller["identifier"],
                top_k=int(arguments.get("top_k", 5) or 5),
            )
            import json as _json
            return self._tool_text(msg_id, _json.dumps({"results": results}))

        if name == "catalog_search":
            if not exposure_allows(graph, "catalog", role):
                self._record_access(graph, "tools/call", "catalog", caller, False,
                                    "catalog not exposed to role")
                return self._error(msg_id, _FORBIDDEN, "catalog is not exposed to you")
            self._record_access(graph, "tools/call", "catalog", caller, True, "ok")
            entries = self._caller_catalog(graph, caller,
                                           str(arguments.get("query", "")))
            import json as _json
            return self._tool_text(msg_id, _json.dumps(
                {"count": len(entries), "capabilities": entries}))

        # Exposed gateway capability (tool name uses __ for the dot).
        key = name.replace("__", ".")
        if key in self.settings.expose_capabilities:
            if not exposure_allows(graph, f"tool:{key}", role):
                self._record_access(graph, "tools/call", f"tool:{key}", caller,
                                    False, "tool not exposed to role")
                return self._error(msg_id, _FORBIDDEN, f"{key} is not exposed to you")
            self._record_access(graph, "tools/call", f"tool:{key}", caller, True, "ok")
            outcome = self._call_capability(graph, key, arguments, caller)
            import json as _json
            return self._tool_text(msg_id, _json.dumps(outcome),
                                   is_error=bool(outcome.get("error")))

        self._record_access(graph, "tools/call", name, caller, False, "unknown tool")
        return self._error(msg_id, _METHOD_NOT_FOUND, f"unknown tool {name!r}")

    # -- JSON-RPC helpers ---------------------------------------------------------

    @staticmethod
    def _result(msg_id, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": code, "message": message}}

    @staticmethod
    def _tool_text(msg_id, text: str, meta: Optional[dict] = None,
                   is_error: bool = False) -> dict:
        result: dict[str, Any] = {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        }
        if meta:
            result["_meta"] = {k: v for k, v in meta.items() if v is not None}
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
