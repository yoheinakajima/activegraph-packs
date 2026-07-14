"""Consented web research: queries-as-surfaces, gateway-governed ingestion.

ADR 0040 / D060 / ADR 0045. The seed queries are the ingestion plan's
surfaces — each one individually strikeable before approval — and they derive
ONLY from owner-confirmed subject facts plus caller-attested confirmed terms.
An approved plan runs as a bounded campaign (rounds, recorded follow-up
frontier, deterministic stopping rules — ``campaign.py``); the model's search
discovers candidate pages; ingestion happens exclusively through the governed
public-presence gateway (budgeted, recorded, injection-scanned), and findings
join the existing verdict path. Nothing here runs without a bound approved
plan.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from packs.connector_control.plans import (
    bind_plan_execution_fn,
    propose_ingestion_plan_fn,
    register_deferred_plan_execution,
    register_ingestion_plan_executor,
)
from packs.connector_control.tools import record_connector_binding_fn
from packs.subject_profile.projection import owner_alias_set_fn

from .campaign import (
    begin_research_round_fn,
    commit_research_round_fn,
    perform_research_round,
    record_seed_queries_fn,
)
from .settings import WebResearchSettings


RESEARCH_SURFACE_ID = "web_research:owner"
RESEARCH_PROPOSER = "web_research.plan_proposer@0.3.0"
_MAX_QUERIES = 4
# Display attributes that may join outward queries. Aliases (handle/url)
# come through the alias set; emails NEVER qualify (D060).
_QUERY_TERM_ATTRIBUTES = ("name", "company")


def _stable(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()}"


def derive_research_queries(
    reader, *, confirmed_terms: tuple[str, ...] = ()
) -> tuple[list[str], list[str]]:
    """Queries from owner-confirmed material only (D060 disclosure boundary).

    Promoted name/company facts and the alias set (handles, confirmed
    domains) qualify; email addresses never do — an address in an outward
    query is a disclosure the owner did not make. ``confirmed_terms`` lets a
    host add terms the owner explicitly typed and confirmed; the caller
    attests that confirmation.
    """
    aliases = owner_alias_set_fn(reader)
    provenance: list[str] = list(aliases.get("fact_refs") or [])
    terms: dict[str, str] = {}
    for fact in reader.objects(type="subject_fact"):
        data = fact.data or {}
        if data.get("subject_ref") != "owner" or data.get("status") != "promoted":
            continue
        attribute = str(data.get("attribute") or "")
        value = str(data.get("value") or "").strip()
        if attribute in _QUERY_TERM_ATTRIBUTES and value and attribute not in terms:
            terms[attribute] = value
            provenance.append(str(data.get("fact_identity") or fact.id))

    queries: list[str] = []

    def _push(query: str) -> None:
        if query and query not in queries:
            queries.append(query)

    for term in confirmed_terms:
        cleaned = str(term).strip()
        if cleaned:
            _push(f'"{cleaned}"')
    name, company = terms.get("name"), terms.get("company")
    if name and company:
        _push(f'"{name}" {company}')  # the strongest disambiguator first
    elif name:
        _push(f'"{name}"')
    for handle in aliases.get("handles") or []:
        _push(f'"@{handle}"')
    for domain in aliases.get("domains") or []:
        _push(domain)
    return queries[:_MAX_QUERIES], sorted(set(provenance))


def derive_scope_terms(
    reader, *, confirmed_terms: tuple[str, ...] = ()
) -> list[str]:
    """The confirmed identity/entity scope a campaign may research: the same
    owner-confirmed material queries derive from, as bare terms the scope
    gate can anchor follow-ups against."""
    aliases = owner_alias_set_fn(reader)
    scope: list[str] = []

    def _push(term: str) -> None:
        cleaned = str(term).strip()
        if cleaned and cleaned not in scope:
            scope.append(cleaned)

    for term in confirmed_terms:
        _push(term)
    for fact in reader.objects(type="subject_fact"):
        data = fact.data or {}
        if data.get("subject_ref") != "owner" or data.get("status") != "promoted":
            continue
        if str(data.get("attribute") or "") in _QUERY_TERM_ATTRIBUTES:
            _push(str(data.get("value") or ""))
    for handle in aliases.get("handles") or []:
        _push(handle)
        _push(f"@{handle}")
    for domain in aliases.get("domains") or []:
        _push(domain)
    return scope


def propose_web_research_plan_fn(
    graph,
    *,
    confirmed_terms: tuple[str, ...] = (),
    search_available: bool = True,
    exclusions: tuple[str, ...] = (),
    settings: Optional[WebResearchSettings] = None,
    reader=None,
) -> dict[str, Any]:
    """Derive and record the research campaign offer; the owner strikes
    queries or approves. The campaign disclosure (rounds, budgets, follow-up
    policy, provider) is part of the plan the owner approves (ADR 0045)."""
    view = reader or graph
    settings = settings or WebResearchSettings()
    queries, provenance = derive_research_queries(
        view, confirmed_terms=tuple(confirmed_terms)
    )
    if not queries:
        return {"ok": False, "reason": "no_confirmed_material", "queries": []}
    policy = next(
        (
            obj for obj in view.objects(type="connector_operational_policy")
            if obj.data.get("status") == "active"
        ),
        None,
    )
    if policy is None:
        raise ValueError("no active connector_operational_policy")
    max_pages = min(
        int(settings.max_pages), int(policy.data.get("max_acquisition_items") or 10)
    )
    max_total_queries = min(
        int(settings.max_total_queries),
        int(policy.data.get("max_provider_calls") or settings.max_total_queries),
    )
    from packs.llm_provider import configured_llm_provider, default_model_for

    resolved = configured_llm_provider()
    provider_disclosure = {
        "provider": resolved.provider,
        "model": default_model_for(resolved),
    }
    availability = (
        f"search runs through your configured model ({resolved.provider})"
        if search_available and resolved.provider
        else "no search-capable model key is configured — approving records the intent; research runs once one is added"
    )
    follow_up_line = (
        f"it may add up to {settings.max_follow_ups_per_round} follow-up "
        "searches per round from what it finds — every one recorded before it "
        "runs, only about you and what you've confirmed; anything wider pauses "
        "for your approval"
        if settings.auto_follow_up and settings.max_follow_ups_per_round
        else "follow-up searches wait for your approval"
    )
    summary = (
        f"i'd run up to {settings.max_rounds} rounds starting from "
        f"{len(queries)} searches drawn from what you've confirmed — each one "
        f"listed verbatim below, strikeable before approval. {follow_up_line}. "
        f"hard bounds: {max_total_queries} searches, {max_pages} pages read, "
        f"then it stops and reports why. {availability}. nothing leaves this "
        "machine until you approve."
    )
    campaign = {
        "max_rounds": int(settings.max_rounds),
        "max_total_queries": max_total_queries,
        "max_pages": max_pages,
        "max_follow_ups_per_round": int(settings.max_follow_ups_per_round),
        "auto_follow_up": bool(settings.auto_follow_up),
        "max_findings_per_query": int(settings.max_findings_per_query),
        "timeout_seconds_per_call": float(settings.timeout_seconds_per_call),
        "max_tokens_per_call": int(settings.max_tokens_per_call),
        "min_new_urls_per_round": int(settings.min_new_urls_per_round),
        "scope_terms": derive_scope_terms(view, confirmed_terms=tuple(confirmed_terms)),
        "exclusions": [*settings.exclusions, *[str(t) for t in exclusions if str(t).strip()]],
        "sensitive_topic_terms": list(settings.sensitive_topic_terms),
        "provider_disclosure": provider_disclosure,
        # A deterministic token ceiling by construction: every call is
        # bounded, and the call count is bounded.
        "token_ceiling": max_total_queries * int(settings.max_tokens_per_call),
    }
    return propose_ingestion_plan_fn(
        graph,
        source_surface_id=RESEARCH_SURFACE_ID,
        service="web_research",
        account_ref="owner",
        family="documents",
        window={"kind": "recent_items", "estimated_items": max_pages},
        derivation={
            "basis": "measured_topology",
            "summary": summary,
            "measurements": {
                "confirmed_sources": len(provenance) + len(confirmed_terms),
                "queries": len(queries),
                "max_rounds": int(settings.max_rounds),
                "max_total_queries": max_total_queries,
            },
            "provenance": provenance,
        },
        surfaces=[
            {
                "surface_ref": f"query:{query}",
                "label": query,
                "included": True,
                "expectation": {"estimated_richness": "unmeasured"},
            }
            for query in queries
        ],
        caps={"max_items": max_pages, "max_pages": len(queries)},
        interpretation_stages=[
            "web_research.campaign@rounds",
            "public_presence.gateway@r0",
            "semantic_extraction.profile@candidates",
            "subject_profile.verdicts@owner",
        ],
        proposed_by=RESEARCH_PROPOSER,
        metadata={
            "search_available": bool(search_available),
            "campaign": campaign,
        },
        reader=reader,
    )


def _start_campaign_run(graph, plan):
    """Mint the run, bind the connector plane, and bind the plan BEFORE any
    terminal observation exists (the settlement behavior would otherwise
    race the bind and strand the plan in "executing")."""
    data = plan.data or {}
    plan_identity = str(data.get("plan_identity") or "")
    run = graph.add_object("web_research_run", {
        "run_identity": _stable("web_research", plan_identity),
        "source_surface_id": RESEARCH_SURFACE_ID,
        "plan_identity": plan_identity,
        "queries": [],
        "status": "running",
        "findings": [],
        "urls_planned": [],
        "urls_ingested": 0,
        "model": None,
        "calls": 0,
        "rounds_executed": 0,
        "stop_reason": None,
        "error": None,
        "metadata": {"plan_version": int(data.get("version") or 0)},
    })
    record_connector_binding_fn(
        graph,
        source_surface_id=RESEARCH_SURFACE_ID,
        service="web_research",
        account_ref="owner",
        family="documents",
        active_route="model_search",
        domain_run_type="web_research_run",
        metadata={"adapter": "web_research.campaign@0.1.0"},
    )
    bind_plan_execution_fn(
        graph,
        plan_ref=plan_identity,
        domain_run_id=run.id,
        source_surface_id=RESEARCH_SURFACE_ID,
    )
    record_seed_queries_fn(graph, plan, run.id)
    return run


def _coerce_round_outcome(
    outcome: dict[str, Any], graph, run
) -> dict[str, Any]:
    """Accept the legacy single-shot perform shape ({findings, calls, model,
    error}) beside the round shape ({results, ...}); hosts and old fixtures
    injected the former."""
    if "results" in outcome:
        return outcome
    pending = [
        obj for obj in graph.objects(type="research_query")
        if obj.data.get("plan_identity") == run.data.get("plan_identity")
        and obj.data.get("status") == "approved_auto"
    ]
    pending.sort(key=lambda obj: (int(obj.data.get("round") or 0), obj.data.get("text") or ""))
    error = outcome.get("error")
    findings = list(outcome.get("findings") or [])
    texts = [str(query.data.get("text") or "") for query in pending]
    by_query: dict[str, list[dict[str, Any]]] = {text: [] for text in texts}
    for row in findings:
        tag = str(row.get("query") or "")
        target = tag if tag in by_query else (texts[0] if texts else "")
        if target:
            by_query[target].append({"claim": row.get("claim"), "url": row.get("url")})
    results = []
    for query, text in zip(pending, texts):
        results.append({
            "query_id": query.id,
            "query": text,
            "ok": error is None,
            "findings": by_query.get(text, []),
            "suggested_queries": list(outcome.get("suggested_queries") or []),
            "recommend_continue": None,
            "injection_flags": [],
            "error": error,
            "response_sample": "",
            "response_length": 0,
        })
    return {
        "results": results,
        "provider_kind": outcome.get("provider_kind"),
        "model": outcome.get("model"),
    }


def execute_web_research_plan_fn(
    graph, plan, *, research: Optional[Any] = None
) -> dict[str, Any]:
    """Synchronous campaign executor (registered with connector_control).

    ``research`` injects the perform phase: a dict is the first round's
    outcome (legacy single-shot shape accepted); a callable is invoked per
    round with the round payload. Absent, rounds perform inline.
    """
    run = _start_campaign_run(graph, plan)
    result: dict[str, Any] = {"ok": False}
    first_round = True
    while True:
        begun = begin_research_round_fn(graph, run.id)
        if not begun.get("ok"):
            if begun.get("reason") == "no_pending_queries":
                # Nothing approved to run — settle through the committer so
                # the stop reason lands on the receipt.
                result = commit_research_round_fn(
                    graph, run.id, {"queries": []},
                    {"results": [], "provider_kind": None, "model": None},
                )
            break
        payload = begun["payload"]
        if callable(research):
            outcome = research(payload)
        elif research is not None and first_round:
            outcome = _coerce_round_outcome(research, graph, run)
        else:
            outcome = perform_research_round(payload)
        first_round = False
        result = commit_research_round_fn(graph, run.id, payload, outcome)
        if result.get("stopped") or not result.get("ok"):
            break
    current = graph.get_object(run.id)
    urls = list(current.data.get("urls_planned") or [])
    return {
        "ok": bool(result.get("ok")),
        "run_id": run.id,
        "findings": len(current.data.get("findings") or []),
        "urls": urls,
        "rounds": int(current.data.get("rounds_executed") or 0),
        "stop_reason": current.data.get("stop_reason"),
    }


def prepare_web_research_execution(graph, plan) -> dict[str, Any]:
    """Deferred phase 1 (graph reads only): the included seed queries."""
    del graph
    data = plan.data or {}
    campaign = dict((data.get("metadata") or {}).get("campaign") or {})
    return {
        "queries": [
            str(surface.get("label") or "")
            for surface in data.get("surfaces") or []
            if surface.get("included") and str(surface.get("label") or "").strip()
        ],
        "max_findings_per_query": int(campaign.get("max_findings_per_query") or 5),
        "max_follow_ups": int(campaign.get("max_follow_ups_per_round") or 0),
        "timeout_seconds": float(campaign.get("timeout_seconds_per_call") or 90.0),
        "max_tokens": int(campaign.get("max_tokens_per_call") or 1_500),
    }


def perform_prepared_web_research(payload: dict[str, Any]) -> dict[str, Any]:
    """Deferred phase 2: network only, zero graph access."""
    return perform_research_round(payload)


def commit_web_research_execution(
    graph, plan, payload: dict[str, Any], outcome: dict[str, Any]
) -> dict[str, Any]:
    """Deferred phase 3: start the campaign run and commit round 1 —
    settlement semantics stay identical to the synchronous executor (D061).
    Rounds ≥ 2 continue through ``pending_research_rounds_fn`` on the host
    pump; the plan settles when the campaign records its stop reason."""
    del payload
    run = _start_campaign_run(graph, plan)
    if "results" not in outcome:
        outcome = _coerce_round_outcome(outcome, graph, run)
    committed = commit_research_round_fn(graph, run.id, {}, outcome)
    return {
        "ok": bool(committed.get("ok")),
        "run_id": run.id,
        "stopped": bool(committed.get("stopped")),
        "stop_reason": committed.get("stop_reason"),
    }


def register_web_research() -> None:
    register_ingestion_plan_executor("web_research", execute_web_research_plan_fn)
    register_deferred_plan_execution(
        "web_research",
        prepare=prepare_web_research_execution,
        perform=perform_prepared_web_research,
        commit=commit_web_research_execution,
    )


__all__ = [
    "RESEARCH_SURFACE_ID",
    "commit_web_research_execution",
    "derive_research_queries",
    "derive_scope_terms",
    "execute_web_research_plan_fn",
    "perform_prepared_web_research",
    "prepare_web_research_execution",
    "propose_web_research_plan_fn",
    "register_web_research",
]
