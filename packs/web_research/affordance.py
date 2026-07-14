"""Web research's understanding affordance (ADR 0047 §2).

Public research joins governed campaigns through the same declaration as
any other source. Query/page/round semantics stay in this pack — the
outward gate wraps the deterministic scope classifier over the CURRENT
approved plan, so a coordinator can never widen disclosure past what the
owner approved (ADR 0045 pre-registration rules remain binding).
"""

from __future__ import annotations

from typing import Any

from .campaign import campaign_config, scope_gate_for_query
from .plan import RESEARCH_SURFACE_ID

WEB_UNDERSTANDING_AFFORDANCE_ID = "web_research_understanding"


def _current_research_plan(reader):
    rows = [
        obj for obj in reader.objects(type="connector_ingestion_plan")
        if obj.data.get("service") == "web_research"
        and obj.data.get("status") not in ("superseded",)
    ]
    rows.sort(key=lambda obj: int(obj.data.get("version") or 0))
    return rows[-1] if rows else None


def research_outward_gate(
    reader, query_text: str, params: dict[str, Any]
) -> dict[str, str]:
    """Classify one proposed outward query against the approved plan:
    auto / amendment / rejected — the source-owned seam the coordinator's
    deterministic validator calls."""
    plan = _current_research_plan(reader)
    if plan is None or plan.data.get("status") not in ("approved", "executing", "fulfilled"):
        return {
            "verdict": "rejected",
            "reason_kind": "no_plan",
            "reason_detail": "no approved research plan covers outward queries",
        }
    config = campaign_config(plan.data or {})
    prior = [
        str(obj.data.get("text") or "")
        for obj in reader.objects(type="research_query")
        if obj.data.get("plan_identity") == plan.data.get("plan_identity")
    ]
    return scope_gate_for_query(
        query_text,
        scope_terms=config["scope_terms"],
        prior_query_texts=prior,
        exclusions=config["exclusions"],
        sensitive_terms=config["sensitive_topic_terms"],
    )


def _research_available(reader) -> dict[str, Any]:
    plan = _current_research_plan(reader)
    if plan is None:
        return {"available": False, "reason": "no research plan proposed yet"}
    status = str(plan.data.get("status") or "")
    if status == "abandoned":
        return {"available": False, "reason": "the owner declined this research plan"}
    if status == "proposed":
        return {"available": False, "reason": "research plan awaits owner approval"}
    return {"available": True, "reason": ""}


#: The affordance declaration. Research findings never allow raw drill-down:
#: pages already ingest through the governed public-presence gateway, and
#: the recorded findings ARE the bounded reading surface.
WEB_UNDERSTANDING_AFFORDANCE: dict[str, Any] = {
    "affordance_id": WEB_UNDERSTANDING_AFFORDANCE_ID,
    "version": "0.1.0",
    "service": "web_research",
    "family": "documents",
    "teaches": [
        "public_identity", "public_work", "organizations", "publications",
        "public_presence", "topics",
    ],
    "capabilities": [
        {
            "capability": "model_search",
            "action_class": "R0",
            "scopes": ["owner_confirmed_identity"],
        },
    ],
    "schemas": {
        "input": {"query": "str", "derived_from_entries": "list[entry_id]"},
        "output": {"findings": [{"claim": "str", "url": "str"}]},
        "evidence_ref": "web_research_run finding url / ingested page evidence",
    },
    "privacy": {
        "excludes": ["private_identifiers", "sensitive_topics_without_amendment"],
        "recipients": "search provider receives approved query text only",
        "content": "public web content only",
        "outward_disclosure": "public_queries",
    },
    "reductions": {
        "leaf_schema": ["topics", "projects", "people", "confidence", "uncertainty"],
    },
    "drill_down": {"allowed": False},
    "bounds": {
        "max_items": 20,
        "max_seconds": 1_800,
        "max_tokens": 60_000,
        "max_cost_milli": 2_000,
    },
    "moves": ["inspect_source", "outward_query"],
    "destinations": ["subject_profile", "projects", "entity_relationships"],
    "coverage_required": True,
    "available": _research_available,
    "outward_gate": research_outward_gate,
    "source_surface_id": RESEARCH_SURFACE_ID,
}


def register_web_research_affordance() -> None:
    from packs.subject_synthesis.affordance import register_understanding_affordance

    register_understanding_affordance(WEB_UNDERSTANDING_AFFORDANCE)


__all__ = [
    "WEB_UNDERSTANDING_AFFORDANCE",
    "WEB_UNDERSTANDING_AFFORDANCE_ID",
    "register_web_research_affordance",
    "research_outward_gate",
]
