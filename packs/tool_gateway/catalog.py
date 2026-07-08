"""Capability catalog — the queryable inventory of everything registered.

MCP multiplied the capability count, and a hardcoded allow-list stops
being a governance story the moment the agent can't enumerate what it is
allowed to do. The catalog closes that gap: every registered capability
is queryable (name, provider, risk class, origin, LLM-exposability,
whether it is on the current allow-list), and the agent searches the
catalog through a governed tool instead of memorizing a list.

Three consumers, three access paths:

  * **The agent** — `catalog.search`, itself a registered low-risk
    capability (register_catalog_capability), so every catalog query is
    a recorded, policy-checked call like any other. The agent can see
    what exists beyond its allow-list (allowed_now=False entries) and
    ask the owner for access; it cannot call what the allow-list
    doesn't grant.
  * **Humans / the Inspector** — `catalog_entries()` directly, or the
    demo server's GET /capabilities.
  * **Inbound MCP callers** — the mcp pack wraps the catalog behind its
    exposure rules (see packs/mcp/server.py): a caller sees only the
    capabilities their role can actually reach. Discovery for
    authorized callers, no reconnaissance surface for anyone else.

The catalog reflects the LIVE registry — it is a view, not a second
source of truth.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from .tools import _LOCAL_REGISTRY, CapabilitySpec, register_local_capability
from .untrusted import NEVER_LLM_CALLABLE


def _entry(spec: CapabilitySpec, allow_list: Optional[list[str]]) -> dict[str, Any]:
    never = spec.capability_name in NEVER_LLM_CALLABLE
    return {
        "key": spec.key,
        "provider": spec.provider_name,
        "capability": spec.capability_name,
        "description": spec.description,
        "risk_class": spec.risk_class,
        "origin": spec.origin,
        "credential_ref": spec.credential_ref_name,  # name only, never a value
        "llm_exposable": (spec.input_schema is not None) and not never,
        "never_llm_callable": never,
        "allowed_now": (spec.key in allow_list) if allow_list is not None else None,
    }


def catalog_entries(
    *,
    allow_list: Optional[list[str]] = None,
    query: Optional[str] = None,
    risk_class: Optional[str] = None,
    origin: Optional[str] = None,
) -> list[dict[str, Any]]:
    """The full catalog, optionally filtered. Sorted by key for determinism.

    *allow_list* (when provided) annotates each entry with allowed_now —
    whether the current chat allow-list grants it. *query* is a
    case-insensitive substring match over key + description. *origin*
    matches exactly ("native") or by prefix ("mcp" matches every
    "mcp:<server>").
    """
    entries = []
    for spec in _LOCAL_REGISTRY.values():
        entry = _entry(spec, allow_list)
        if query:
            haystack = f"{entry['key']} {entry['description']}".lower()
            if query.lower() not in haystack:
                continue
        if risk_class and entry["risk_class"] != risk_class:
            continue
        if origin and not (entry["origin"] == origin
                           or entry["origin"].startswith(f"{origin}:")):
            continue
        entries.append(entry)
    entries.sort(key=lambda e: e["key"])
    return entries


class CatalogSearchInput(BaseModel):
    query: str = Field(
        default="",
        description="Case-insensitive substring over capability key and description.",
    )
    risk_class: str = Field(
        default="",
        description="Filter by risk class: low | medium | high | critical.",
    )
    origin: str = Field(
        default="",
        description="Filter by origin: 'native', 'mcp', or 'mcp:<server>'.",
    )


def register_catalog_capability(
    allow_list_fn: Optional[Callable[[], list[str]]] = None,
    *,
    risk_class: str = "low",
) -> CapabilitySpec:
    """Register ``catalog.search`` so the agent queries the catalog through
    the governed path.

    *allow_list_fn* returns the CURRENT chat allow-list (a callable so the
    annotation stays live if the host reconfigures); None means entries
    carry allowed_now=null. Read-only, low risk: discovery should be
    cheap, acting stays gated.
    """

    def _search(query: str = "", risk_class: str = "", origin: str = "",
                execution_context: Optional[dict] = None) -> dict:
        entries = catalog_entries(
            allow_list=allow_list_fn() if allow_list_fn else None,
            query=query or None,
            risk_class=risk_class or None,
            origin=origin or None,
        )
        return {"count": len(entries), "capabilities": entries}

    return register_local_capability(
        "catalog", "search", _search,
        input_schema=CatalogSearchInput,
        description=(
            "Search the capability catalog: every registered capability with "
            "its risk class, origin (native vs MCP-derived), and whether it "
            "is on your current allow-list. Use this instead of guessing "
            "tool names; ask the owner to allow-list anything you need."
        ),
        risk_class=risk_class,
    )
