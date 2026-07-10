"""Gateway-side registration enforcement (the Q8 chain, step 3).

The manifest chain closes here. Step 1 is the manifest declaration
(Pack.capabilities, verified two-way against source by the runtime's
verify_surface and by CI's AST check). Step 2 is the runtime emitting
that declaration into the graph (the pack.loaded payload carries every
declared capability with its risk class; pack.disabled marks the
surface deregistered). Step 3, this module: at
register_local_capability time, the gateway resolves the registering
capability against those graph-derived declarations and refuses three
things:

  * an UNDECLARED (provider, capability) pair: no loaded pack declared
    it, so no reviewer ever saw it on a manifest;
  * RISK-CLASS DRIFT between the declaration and the registration call
    (the swap a decision surface must not miss: reviewed as low,
    registered as something that auto-approves differently);
  * a registration claiming a DISABLED pack's surface (disable means
    disabled; the deregistered surface must not crawl back in through
    the capability registry).

Enforcement is ARMED by the host, once, with the live graph
(`arm_registration_enforcement(graph)`); from that moment every native
registration is checked and a hostile late-loaded pack cannot opt out
by omitting a kwarg. Unarmed, registration behaves as before (bare
hosts, unit fixtures). Non-native origins (origin="mcp:<server>") are
exempt from the declared-pair requirement: those registrations are
host-mediated by the MCP registry, whose trust model is the exposure
rules, never a pack manifest. The CI AST check stays as
defense-in-depth at review time; this is the runtime half.
"""

from __future__ import annotations

from typing import Any, Optional

_ENFORCEMENT_GRAPH: Optional[Any] = None


def arm_registration_enforcement(graph) -> None:
    """Host call, once, after the trusted boot loads finish. Every
    native registration from here on is checked against the graph."""
    global _ENFORCEMENT_GRAPH
    _ENFORCEMENT_GRAPH = graph


def disarm_registration_enforcement() -> None:
    """Fixture/teardown seam."""
    global _ENFORCEMENT_GRAPH
    _ENFORCEMENT_GRAPH = None


def _pack_states(graph) -> dict[str, dict[str, Any]]:
    """{pack_name: {"disabled": bool,
                    "capabilities": {(prov, cap): (risk, action)}}}
    derived from the graph's pack.loaded / pack.disabled events, latest
    event wins (a re-load after a disable re-enables). The action class
    is "" when the declaration omitted it (pre-v1.9 payloads carry no
    action_class key at all)."""
    states: dict[str, dict[str, Any]] = {}
    for event in graph.events:
        if event.type == "pack.loaded":
            payload = event.payload or {}
            name = str(payload.get("name", ""))
            states[name] = {
                "disabled": False,
                "capabilities": {
                    (str(c.get("provider", "")), str(c.get("capability", ""))):
                        (str(c.get("risk_class", "")),
                         str(c.get("action_class", "")))
                    for c in (payload.get("capabilities") or [])
                },
            }
        elif event.type == "pack.disabled":
            payload = event.payload or {}
            name = str(payload.get("name", ""))
            entry = states.setdefault(name, {"capabilities": {}})
            entry["disabled"] = True
    return states


def check_registration(provider_name: str, capability_name: str,
                       risk_class: str, origin: str = "native",
                       action_class: str = "") -> None:
    """Raises ValueError when an armed graph refuses the registration.

    No-op when unarmed or when the origin is not native (MCP-origin
    registrations are governed by exposure rules, not manifests)."""
    graph = _ENFORCEMENT_GRAPH
    if graph is None or origin != "native":
        return
    pair = (provider_name, capability_name)
    key = f"{provider_name}.{capability_name}"
    states = _pack_states(graph)
    declaring = {name: state for name, state in states.items()
                 if pair in state["capabilities"]}
    if not declaring:
        raise ValueError(
            f"registration refused: no loaded pack declares capability "
            f"{key!r}. Declare it in the pack's Pack.capabilities (and "
            f"manifest.toml [surface.capabilities]) so reviewers see it, "
            f"or register before enforcement is armed for host-owned "
            f"capabilities."
        )
    enabled = {name: state for name, state in declaring.items()
               if not state.get("disabled")}
    if not enabled:
        names = ", ".join(sorted(declaring))
        raise ValueError(
            f"registration refused: capability {key!r} is declared only "
            f"by disabled pack(s) ({names}). Disable means disabled; "
            f"re-enable is a fresh adoption, not a registry write."
        )
    declared_risks = {state["capabilities"][pair][0]
                      for state in enabled.values()}
    if risk_class not in declared_risks:
        declared = ", ".join(sorted(declared_risks))
        raise ValueError(
            f"registration refused: capability {key!r} declared "
            f"risk_class {declared!r} but the registration call says "
            f"{risk_class!r}. Risk drift between the reviewed manifest "
            f"and the live registry is exactly the swap the decision "
            f"surface must not miss; fix the declaration or the call."
        )
    # The same drift rule for the canonical dimension (ADR 0016): a
    # capability reviewed as R0 must not register as R2 — and one
    # reviewed WITHOUT a class must not register with one (or vice
    # versa), because presence itself changes automation eligibility.
    declared_actions = {state["capabilities"][pair][1]
                        for state in enabled.values()}
    if action_class not in declared_actions:
        declared = ", ".join(sorted(repr(a) for a in declared_actions))
        raise ValueError(
            f"registration refused: capability {key!r} declared "
            f"action_class {declared} but the registration call says "
            f"{action_class!r}. Action-class drift between the reviewed "
            f"manifest and the live registry changes authority; fix the "
            f"declaration or the call."
        )
