"""Consented web research: queries-as-surfaces, gateway-governed ingestion.

ADR 0040 / D060. The queries are the ingestion plan's surfaces — each one
individually strikeable before approval — and they derive ONLY from
owner-confirmed subject facts plus caller-attested confirmed terms. The
model's search discovers candidate pages; ingestion happens exclusively
through the governed public-presence gateway (budgeted, recorded,
injection-scanned), and its findings join the existing verdict path.
Nothing here runs without a bound approved plan.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

from packs.connector_control.plans import (
    bind_plan_execution_fn,
    propose_ingestion_plan_fn,
    register_deferred_plan_execution,
    register_ingestion_plan_executor,
)
from packs.connector_control.tools import (
    record_connector_binding_fn,
    record_connector_learning_delta_fn,
    record_connector_native_view_fn,
    record_connector_run_observation_fn,
)
from packs.importers.public_presence.tools import bootstrap_public_presence_fn
from packs.subject_profile.projection import owner_alias_set_fn


RESEARCH_SURFACE_ID = "web_research:owner"
RESEARCH_PROPOSER = "web_research.plan_proposer@0.2.0"
_MAX_QUERIES = 4
_MAX_FINDINGS_PER_QUERY = 5
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
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


def propose_web_research_plan_fn(
    graph,
    *,
    confirmed_terms: tuple[str, ...] = (),
    search_available: bool = True,
    reader=None,
) -> dict[str, Any]:
    """Derive and record the research offer; the owner strikes or approves."""
    view = reader or graph
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
    max_pages_to_ingest = min(10, int(policy.data.get("max_acquisition_items") or 10))
    availability = (
        "search runs through your configured model"
        if search_available
        else "no search-capable model key is configured — approving records the intent; research runs once one is added"
    )
    summary = (
        f"i'd run {len(queries)} searches drawn from what you've confirmed — "
        f"each one listed verbatim below, strikeable before approval; up to "
        f"{max_pages_to_ingest} discovered pages ingest through the recorded "
        f"gateway. {availability}. nothing leaves this machine until you approve."
    )
    return propose_ingestion_plan_fn(
        graph,
        source_surface_id=RESEARCH_SURFACE_ID,
        service="web_research",
        account_ref="owner",
        family="documents",
        window={"kind": "recent_items", "estimated_items": max_pages_to_ingest},
        derivation={
            "basis": "measured_topology",
            "summary": summary,
            "measurements": {
                "confirmed_sources": len(provenance) + len(confirmed_terms),
                "queries": len(queries),
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
        caps={"max_items": max_pages_to_ingest, "max_pages": len(queries)},
        interpretation_stages=[
            "public_presence.gateway@r0",
            "semantic_extraction.profile@candidates",
            "subject_profile.verdicts@owner",
        ],
        proposed_by=RESEARCH_PROPOSER,
        metadata={"search_available": bool(search_available)},
        reader=reader,
    )


def _research_prompt(query: str, limit: int) -> tuple[str, str]:
    system = (
        "You research a person's public professional presence. Use web search. "
        "Only report claims directly supported by a page you actually found. "
        "Respond with STRICT JSON only, no prose."
    )
    user = (
        f"Search the web for: {query}\n"
        f"Return at most {limit} findings as JSON: "
        '{"findings": [{"claim": "<one factual sentence>", "url": "<exact source url>"}]}'
    )
    return system, user


def _parse_findings(text: str) -> list[dict[str, str]]:
    from packs.llm_provider import parse_json_payload

    payload = parse_json_payload(text) or {}
    findings = []
    for row in payload.get("findings") or []:
        claim = str((row or {}).get("claim") or "").strip()
        url = str((row or {}).get("url") or "").strip()
        if claim and _URL_RE.match(url):
            findings.append({"claim": claim[:500], "url": url})
    return findings


def perform_research_queries(
    queries: list[str],
    *,
    provider=None,
    model: Optional[str] = None,
    max_findings_per_query: int = _MAX_FINDINGS_PER_QUERY,
    timeout_seconds: float = 90.0,
) -> dict[str, Any]:
    """Provider-only phase (ADR 0041 shape): zero graph access.

    Uses the configured model's server-side web search where the provider
    supports it. Failure of one query never poisons the rest.
    """
    from packs.llm_provider import configured_llm_provider, default_model_for, get_llm_provider

    resolved = configured_llm_provider()
    if provider is None:
        if not resolved.configured:
            return {"findings": [], "calls": 0, "model": None,
                    "error": "research_provider_unavailable"}
        provider = get_llm_provider()
    model = model or default_model_for(resolved) or ""
    from activegraph.llm import LLMMessage

    findings: list[dict[str, str]] = []
    errors: list[str] = []
    responses: list[dict[str, Any]] = []
    calls = 0
    for query in queries:
        system, user = _research_prompt(query, max_findings_per_query)
        try:
            response = provider.complete(
                system=system,
                messages=[LLMMessage(role="user", content=user)],
                model=model,
                max_tokens=1_500,
                temperature=0.0,
                top_p=1.0,
                output_schema=None,
                timeout_seconds=timeout_seconds,
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 3,
                }],
            )
            calls += 1
            text = getattr(response, "text", "") or ""
            parsed = _parse_findings(text)
            # A bounded breadcrumb per query: "found nothing" must always be
            # diagnosable from the run record alone (ADR 0043).
            responses.append({
                "query": query,
                "length": len(text),
                "parsed_findings": len(parsed),
                "sample": text[:400],
            })
            for finding in parsed:
                finding["query"] = query
                findings.append(finding)
        except Exception as exc:  # one query's failure stays one query's failure
            errors.append(f"{query}: {type(exc).__name__}: {exc}"[:300])
    return {
        "findings": findings,
        "calls": calls,
        "model": model,
        "responses": responses,
        "error": "; ".join(errors)[:500] if errors else None,
    }


def execute_web_research_plan_fn(
    graph, plan, *, research: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    """Executor for the approved plan (registered with connector_control).

    ``research`` lets a host inject the perform-phase result it ran off the
    engine thread; when absent the call runs inline (bounded: ≤3 queries).
    """
    data = plan.data or {}
    queries = [
        str(surface.get("label") or "")
        for surface in data.get("surfaces") or []
        if surface.get("included") and str(surface.get("label") or "").strip()
    ]
    caps = dict(data.get("caps") or {})
    plan_identity = str(data.get("plan_identity") or "")
    run = graph.add_object("web_research_run", {
        "run_identity": _stable("web_research", plan_identity),
        "source_surface_id": RESEARCH_SURFACE_ID,
        "plan_identity": plan_identity,
        "queries": queries,
        "status": "running",
        "findings": [],
        "urls_planned": [],
        "urls_ingested": 0,
        "model": None,
        "calls": 0,
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
        metadata={"adapter": "web_research.plan@0.1.0"},
    )
    # Bind before any terminal observation exists: this executor settles its
    # run synchronously, so a post-hoc bind would race the neutral
    # plan-settlement behavior and strand the plan in "executing".
    bind_plan_execution_fn(
        graph,
        plan_ref=plan_identity,
        domain_run_id=run.id,
        source_surface_id=RESEARCH_SURFACE_ID,
    )

    outcome = research if research is not None else perform_research_queries(queries)
    findings = list(outcome.get("findings") or [])
    urls = list(dict.fromkeys(
        finding["url"] for finding in findings if _URL_RE.match(finding.get("url") or "")
    ))[: int(caps.get("max_items") or 10)]

    ingested = 0
    if urls:
        presence = bootstrap_public_presence_fn(
            graph,
            {"urls": urls},
            source_surface_id=RESEARCH_SURFACE_ID,
            budget=int(caps.get("max_items") or 10),
            requested_by="web_research.executor",
        )
        ingested = int(presence.get("proposed_calls") or 0)

    hard_failed = bool(outcome.get("error")) and not findings and not urls
    status = "failed" if hard_failed else ("completed" if findings else "partial")
    graph.patch_object(run.id, {
        "status": status,
        "findings": findings,
        "urls_planned": urls,
        "urls_ingested": ingested,
        "model": outcome.get("model"),
        "calls": int(outcome.get("calls") or 0),
        "error": outcome.get("error"),
        "metadata": {
            "plan_version": int(data.get("version") or 0),
            # Bounded per-query breadcrumbs (ADR 0043): zero findings must
            # be diagnosable from the run record alone.
            "responses": list(outcome.get("responses") or [])[:8],
        },
    }, rationale="web research settled")

    state = {"completed": "succeeded", "partial": "partial", "failed": "failed"}[status]
    record_connector_run_observation_fn(
        graph,
        domain_run_id=run.id,
        source_surface_id=RESEARCH_SURFACE_ID,
        service="web_research",
        account_ref="owner",
        family="documents",
        route="model_search",
        state=state,
        phase="served" if state == "succeeded" else state,
        mode="backfill",
        attempt=True,
        bounds={"max_items": int(caps.get("max_items") or 0), "max_pages": len(queries)},
        counts={"findings": len(findings), "pages": ingested, "calls": int(outcome.get("calls") or 0)},
        metadata={"plan_identity": plan_identity, "plan_version": int(data.get("version") or 0)},
    )
    record_connector_learning_delta_fn(
        graph,
        domain_run_id=run.id,
        source_surface_id=RESEARCH_SURFACE_ID,
        service="web_research",
        family="documents",
        status={"succeeded": "complete", "partial": "partial", "failed": "failed"}[state],
        evidence={"created": ingested, "updated": 0, "deleted": 0},
        refs=[run.id],
        plan={
            "plan_identity": plan_identity,
            "plan_version": int(data.get("version") or 0),
            "planned": {"max_items": int(caps.get("max_items") or 0), "max_pages": len(queries)},
            "actual": {"imported": ingested, "pages": int(outcome.get("calls") or 0)},
            "within_bounds": ingested <= int(caps.get("max_items") or 0),
        },
    )
    record_connector_native_view_fn(
        graph,
        source_surface_id=RESEARCH_SURFACE_ID,
        service="web_research",
        family="documents",
        state="ready" if urls else ("failed" if state == "failed" else "empty"),
        data={
            "items": [
                {"item_ref": url, "title": url, "kind": "page"} for url in urls
            ],
            "total_count": len(urls),
        },
        refs=[run.id],
    )
    return {"ok": state != "failed", "run_id": run.id, "findings": len(findings), "urls": urls}


def prepare_web_research_execution(graph, plan) -> dict[str, Any]:
    """Deferred phase 1 (graph reads only): the included queries."""
    del graph
    data = plan.data or {}
    return {
        "queries": [
            str(surface.get("label") or "")
            for surface in data.get("surfaces") or []
            if surface.get("included") and str(surface.get("label") or "").strip()
        ],
    }


def perform_prepared_web_research(payload: dict[str, Any]) -> dict[str, Any]:
    """Deferred phase 2: network only, zero graph access."""
    return perform_research_queries(list(payload.get("queries") or []))


def commit_web_research_execution(
    graph, plan, payload: dict[str, Any], outcome: dict[str, Any]
) -> dict[str, Any]:
    """Deferred phase 3: the synchronous executor with the perform result
    injected — settlement semantics stay byte-identical (D061)."""
    del payload
    return execute_web_research_plan_fn(graph, plan, research=outcome)


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
    "execute_web_research_plan_fn",
    "perform_prepared_web_research",
    "perform_research_queries",
    "prepare_web_research_execution",
    "propose_web_research_plan_fn",
    "register_web_research",
]
