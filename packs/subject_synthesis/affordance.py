"""Understanding affordances: how a source joins comprehension (ADR 0047 §2).

An affordance is the small typed declaration by which a tool/source
participates in a governed comprehension campaign: what it can teach, its
canonical capabilities and safe selectable scopes, schemas, privacy and
outward-disclosure rules, supported reductions, drill-down permission and
bounds, budgets, destinations, and coverage/receipt requirements.

The declaration makes a source *discoverable and sequenceable* by the
dynamic coordinator without a bespoke product wizard. It deliberately does
NOT make acquisition generic: Gmail keeps sent-message semantics, web
research keeps query/page semantics, and each capability stays governed by
its service/family contract. ADR 0045's connector comprehension recipe is
the source-specific reduction half; an affordance may reference one.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

AFFORDANCE_CONTRACT_VERSION = "understanding_affordance@0.1.0"

#: The coordinator move kinds a source may serve (ADR 0047 §1). The
#: coordinator may only propose a source move an affordance declares; the
#: campaign-level kinds (align/ask/amend/synthesize/stop) need no affordance.
AFFORDANCE_MOVES = (
    "inspect_source",  # select/read within the approved source scope
    "reduce_fast",     # request fast leaf/aggregate reduction
    "drill_down",      # bounded reasoning-model evidence read
    "outward_query",   # provider-disclosed outward search within plan scope
)

REQUIRED_AFFORDANCE_FIELDS = (
    "affordance_id",
    "version",
    "service",
    "family",
    "teaches",
    "capabilities",
    "schemas",
    "privacy",
    "reductions",
    "drill_down",
    "bounds",
    "moves",
    "destinations",
    "coverage_required",
)

_REQUIRED_BOUNDS = ("max_items", "max_seconds", "max_tokens", "max_cost_milli")
_REQUIRED_DRILL_DOWN = ("allowed",)
_DRILL_DOWN_BOUNDS = ("max_items", "max_excerpt_chars", "max_context_tokens")

_AFFORDANCES: dict[str, dict[str, Any]] = {}


def validate_understanding_affordance(declaration: dict[str, Any]) -> list[str]:
    """Every reason the declaration is malformed (empty list = valid).

    Validation runs at registration so a broken affordance fails at pack
    load, never mid-campaign.
    """
    problems: list[str] = []
    missing = [f for f in REQUIRED_AFFORDANCE_FIELDS if f not in declaration]
    if missing:
        problems.append(f"missing fields: {missing}")
        return problems
    if not str(declaration["affordance_id"]).strip():
        problems.append("affordance_id is required")
    if not str(declaration["version"]).strip():
        problems.append("version is required")
    if not str(declaration["service"]).strip():
        problems.append("service is required (source truth stays service-owned)")
    teaches = declaration["teaches"]
    if not isinstance(teaches, (list, tuple)) or not teaches:
        problems.append("teaches must name at least one owner question/signal category")
    capabilities = declaration["capabilities"]
    if not isinstance(capabilities, (list, tuple)) or not capabilities:
        problems.append("capabilities must declare at least one capability")
    else:
        for row in capabilities:
            if not isinstance(row, dict) or not row.get("capability"):
                problems.append(f"capability rows need a capability id: {row!r}")
                continue
            if not row.get("action_class"):
                problems.append(
                    f"capability {row['capability']!r} needs an action_class"
                )
            if not isinstance(row.get("scopes"), (list, tuple)):
                problems.append(
                    f"capability {row['capability']!r} needs safe selectable scopes"
                )
    schemas = declaration["schemas"]
    if not isinstance(schemas, dict) or not all(
        key in schemas for key in ("input", "output", "evidence_ref")
    ):
        problems.append("schemas must declare input, output, and evidence_ref")
    privacy = declaration["privacy"]
    if not isinstance(privacy, dict) or "outward_disclosure" not in privacy:
        problems.append(
            "privacy must state outward_disclosure "
            "(none | provider_only | public_queries)"
        )
    elif privacy["outward_disclosure"] not in ("none", "provider_only", "public_queries"):
        problems.append(
            f"unknown outward_disclosure {privacy['outward_disclosure']!r}"
        )
    reductions = declaration["reductions"]
    if not isinstance(reductions, dict) or not (
        reductions.get("recipe_id") or reductions.get("leaf_schema")
    ):
        problems.append(
            "reductions must reference a comprehension recipe_id or declare a leaf_schema"
        )
    drill = declaration["drill_down"]
    if not isinstance(drill, dict) or any(k not in drill for k in _REQUIRED_DRILL_DOWN):
        problems.append("drill_down must declare allowed")
    elif drill.get("allowed"):
        for key in _DRILL_DOWN_BOUNDS:
            if not isinstance(drill.get(key), int) or drill[key] <= 0:
                problems.append(f"drill_down.{key} must be a positive bound when allowed")
        if not callable(drill.get("select")):
            problems.append(
                "drill_down.select must be callable(reader, bounded_params) "
                "when drill-down is allowed — selection semantics stay source-owned"
            )
    bounds = declaration["bounds"]
    if not isinstance(bounds, dict):
        problems.append("bounds must be a mapping")
    else:
        for key in _REQUIRED_BOUNDS:
            if not isinstance(bounds.get(key), (int, float)) or bounds[key] <= 0:
                problems.append(f"bounds.{key} must be a positive number")
    moves = declaration["moves"]
    if not isinstance(moves, (list, tuple)) or not moves:
        problems.append("moves must declare at least one supported move kind")
    else:
        unknown = [m for m in moves if m not in AFFORDANCE_MOVES]
        if unknown:
            problems.append(f"unknown moves {unknown}; supported: {AFFORDANCE_MOVES}")
        if "drill_down" in moves and not (isinstance(drill, dict) and drill.get("allowed")):
            problems.append("moves include drill_down but drill_down.allowed is false")
        if "outward_query" in moves:
            if (declaration.get("privacy") or {}).get("outward_disclosure") == "none":
                problems.append(
                    "moves include outward_query but privacy.outward_disclosure is none"
                )
            if not callable(declaration.get("outward_gate")):
                problems.append(
                    "moves include outward_query but no outward_gate callable is "
                    "declared — outward-scope semantics stay source-owned"
                )
    destinations = declaration["destinations"]
    if not isinstance(destinations, (list, tuple)) or not destinations:
        problems.append("destinations must name at least one candidate destination")
    available = declaration.get("available")
    if available is not None and not callable(available):
        problems.append("available must be callable(reader) -> dict when present")
    return problems


def register_understanding_affordance(
    declaration: dict[str, Any], *, replace: bool = True
) -> None:
    """Register one affordance; malformed declarations fail loudly here."""
    problems = validate_understanding_affordance(declaration)
    if problems:
        raise ValueError(
            f"invalid understanding affordance "
            f"{declaration.get('affordance_id')!r}: {problems}"
        )
    affordance_id = str(declaration["affordance_id"]).strip()
    if affordance_id in _AFFORDANCES and not replace:
        raise ValueError(f"understanding affordance already registered: {affordance_id!r}")
    _AFFORDANCES[affordance_id] = dict(declaration)


def unregister_understanding_affordance(affordance_id: str) -> None:
    _AFFORDANCES.pop(affordance_id, None)


def get_understanding_affordance(affordance_id: str) -> Optional[dict[str, Any]]:
    return _AFFORDANCES.get(affordance_id)


def registered_understanding_affordances() -> list[str]:
    return sorted(_AFFORDANCES)


def affordance_catalog_fn(reader) -> list[dict[str, Any]]:
    """The coordinator's discovery view: every registered affordance with its
    availability verdict and the bounded declaration facts a proposer may
    read. Nothing here grants authority — plans and the deterministic
    validator do."""
    catalog: list[dict[str, Any]] = []
    for affordance_id in registered_understanding_affordances():
        declaration = _AFFORDANCES[affordance_id]
        available: dict[str, Any] = {"available": True, "reason": ""}
        probe: Optional[Callable] = declaration.get("available")
        if callable(probe):
            try:
                verdict = probe(reader)
            except Exception as exc:  # a broken probe is an unavailable source
                verdict = {"available": False, "reason": f"probe_error: {exc}"[:200]}
            if isinstance(verdict, dict):
                available = {
                    "available": bool(verdict.get("available")),
                    "reason": str(verdict.get("reason") or "")[:200],
                }
            else:
                available = {"available": bool(verdict), "reason": ""}
        catalog.append({
            "affordance_id": affordance_id,
            "version": str(declaration.get("version") or ""),
            "service": str(declaration.get("service") or ""),
            "family": str(declaration.get("family") or ""),
            "teaches": list(declaration.get("teaches") or []),
            "capabilities": [
                {
                    "capability": str(row.get("capability") or ""),
                    "action_class": str(row.get("action_class") or ""),
                    "scopes": list(row.get("scopes") or []),
                }
                for row in declaration.get("capabilities") or []
            ],
            "moves": list(declaration.get("moves") or []),
            "drill_down": dict(declaration.get("drill_down") or {}),
            "bounds": dict(declaration.get("bounds") or {}),
            "outward_disclosure": str(
                (declaration.get("privacy") or {}).get("outward_disclosure") or "none"
            ),
            "destinations": list(declaration.get("destinations") or []),
            "coverage_required": bool(declaration.get("coverage_required")),
            **available,
        })
    return catalog


__all__ = [
    "AFFORDANCE_CONTRACT_VERSION",
    "AFFORDANCE_MOVES",
    "REQUIRED_AFFORDANCE_FIELDS",
    "affordance_catalog_fn",
    "get_understanding_affordance",
    "register_understanding_affordance",
    "registered_understanding_affordances",
    "unregister_understanding_affordance",
    "validate_understanding_affordance",
]
