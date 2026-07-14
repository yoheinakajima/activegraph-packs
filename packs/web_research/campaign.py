"""The bounded adaptive research campaign (ADR 0045 §1–2).

A campaign is rounds of neutral-adapter searches under an approved plan.
Deterministic budgets are authoritative; every query is recorded before it
executes (seeds as the plan's strikeable surfaces, follow-ups as frontier
rows minted at the previous round's commit); follow-ups derive only from
sanitized structured findings; and a query that would widen scope pauses as
a reviewable amendment — research never silently broadens outward. The stop
reason is part of the receipt.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

from .search_adapter import SearchRequest, perform_neutral_search
from .settings import WebResearchSettings


_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
_QUOTED_RE = re.compile(r'"([^"]+)"')
_HANDLE_RE = re.compile(r"@([\w.-]+)")
_SITE_RE = re.compile(r"site:([\w.-]+)", re.IGNORECASE)
_CAP_SEQ_RE = re.compile(r"\b([A-Z][\w&.-]*(?:\s+[A-Z][\w&.-]*)+)\b")

STOP_REASONS = (
    "max_rounds_reached",
    "query_budget_exhausted",
    "page_budget_exhausted",
    "no_new_findings",
    "frontier_exhausted",
    "provider_failure",
    "provider_unavailable",
    "owner_abandoned",
)


def _stable(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()}"


def _norm(text: str) -> str:
    return " ".join(str(text).lower().split())


def campaign_config(plan_data: dict[str, Any]) -> dict[str, Any]:
    """The plan's approved campaign disclosure, with library defaults for
    stores whose plans predate ADR 0045 (single-round behavior)."""
    campaign = dict((plan_data.get("metadata") or {}).get("campaign") or {})
    defaults = WebResearchSettings()
    caps = dict(plan_data.get("caps") or {})
    return {
        "max_rounds": int(campaign.get("max_rounds") or 1),
        "max_total_queries": int(
            campaign.get("max_total_queries")
            or len(plan_data.get("surfaces") or [])
            or 1
        ),
        "max_pages": int(campaign.get("max_pages") or caps.get("max_items") or 10),
        "max_follow_ups_per_round": int(campaign.get("max_follow_ups_per_round") or 0),
        "auto_follow_up": bool(campaign.get("auto_follow_up", False)),
        "max_findings_per_query": int(
            campaign.get("max_findings_per_query") or defaults.max_findings_per_query
        ),
        "timeout_seconds_per_call": float(
            campaign.get("timeout_seconds_per_call") or defaults.timeout_seconds_per_call
        ),
        "max_tokens_per_call": int(
            campaign.get("max_tokens_per_call") or defaults.max_tokens_per_call
        ),
        "min_new_urls_per_round": int(
            campaign.get("min_new_urls_per_round", defaults.min_new_urls_per_round)
        ),
        "scope_terms": [str(t) for t in campaign.get("scope_terms") or []],
        "exclusions": [str(t) for t in campaign.get("exclusions") or []],
        "sensitive_topic_terms": [
            str(t) for t in campaign.get("sensitive_topic_terms")
            or defaults.sensitive_topic_terms
        ],
    }


# ---- the scope gate ---------------------------------------------------------

def _entity_candidates(query: str) -> list[str]:
    """Deterministically extract the tokens that could name a person or
    organization: quoted phrases, @handles, site:/bare domains, and
    multi-word capitalized sequences."""
    candidates: list[str] = []
    stripped = query
    for match in _QUOTED_RE.finditer(query):
        candidates.append(match.group(1))
        stripped = stripped.replace(match.group(0), " ")
    candidates.extend(match.group(1) for match in _HANDLE_RE.finditer(stripped))
    candidates.extend(match.group(1) for match in _SITE_RE.finditer(stripped))
    candidates.extend(match.group(1) for match in _CAP_SEQ_RE.finditer(stripped))
    return [c.strip() for c in candidates if c.strip()]


def _covered(candidate: str, scope: list[str]) -> bool:
    needle = _norm(candidate)
    if not needle:
        return True
    for term in scope:
        hay = _norm(term)
        if not hay:
            continue
        if needle in hay or hay in needle:
            return True
    return False


def scope_gate_for_query(
    text: str,
    *,
    scope_terms: list[str],
    prior_query_texts: list[str],
    exclusions: list[str],
    sensitive_terms: list[str],
) -> dict[str, str]:
    """Classify one follow-up query (ADR 0045 §2).

    verdict: auto (in scope), amendment (needs renewed approval),
    rejected (private identifiers and owner-excluded terms never run —
    an email or phone number in an outward query is a disclosure the owner
    did not make, D060).
    """
    lowered = _norm(text)
    if _EMAIL_RE.search(text):
        return {"verdict": "rejected", "reason_kind": "new_entity",
                "reason_detail": "private identifier (email address) may never join an outward query"}
    if _PHONE_RE.search(text):
        return {"verdict": "rejected", "reason_kind": "new_entity",
                "reason_detail": "private identifier (phone number) may never join an outward query"}
    for term in exclusions:
        if term and _norm(term) in lowered:
            return {"verdict": "rejected", "reason_kind": "excluded_term",
                    "reason_detail": f"owner excluded {term!r} from this campaign"}
    for term in sensitive_terms:
        if term and _norm(term) in lowered:
            return {"verdict": "amendment", "reason_kind": "sensitive_topic",
                    "reason_detail": f"touches sensitive topic {term!r}"}
    scope = list(scope_terms) + list(prior_query_texts)
    if not any(_covered(term, [lowered]) for term in scope_terms if term):
        return {"verdict": "amendment", "reason_kind": "new_entity",
                "reason_detail": "no confirmed-scope anchor in the query"}
    for candidate in _entity_candidates(text):
        if not _covered(candidate, scope):
            return {"verdict": "amendment", "reason_kind": "new_entity",
                    "reason_detail": f"introduces {candidate!r}, outside the approved scope"}
    return {"verdict": "auto", "reason_kind": "", "reason_detail": ""}


# ---- frontier bookkeeping ---------------------------------------------------

def _frontier_rows(reader, plan_identity: str):
    rows = [
        obj for obj in reader.objects(type="research_query")
        if obj.data.get("plan_identity") == plan_identity
    ]
    rows.sort(key=lambda obj: (int(obj.data.get("round") or 0), obj.data.get("text") or ""))
    return rows


def record_seed_queries_fn(graph, plan, run_ref: str) -> list[Any]:
    """Frontier rows for the plan's approved seed surfaces (round 1). The
    plan itself pre-registered them; these rows are the execution ledger."""
    data = plan.data or {}
    plan_identity = str(data.get("plan_identity") or "")
    existing = {
        _norm(obj.data.get("text") or "") for obj in _frontier_rows(graph, plan_identity)
    }
    rows = []
    for surface in data.get("surfaces") or []:
        if not surface.get("included"):
            continue
        text = str(surface.get("label") or "").strip()
        if not text or _norm(text) in existing:
            continue
        rows.append(graph.add_object("research_query", {
            "query_identity": _stable("research_query", plan_identity, _norm(text)),
            "plan_identity": plan_identity,
            "run_ref": run_ref,
            "text": text,
            "origin": "seed",
            "round": 1,
            "scope_entity": "owner",
            "expected_gain": "seed coverage of the owner's confirmed identity",
            "status": "approved_auto",
        }))
        existing.add(_norm(text))
    return rows


def record_follow_up_queries_fn(
    graph,
    plan,
    run_ref: str,
    *,
    suggestions: list[dict[str, Any]],
    next_round: int,
    config: dict[str, Any],
    remaining_query_budget: int,
) -> dict[str, int]:
    """Gate and record model-suggested follow-ups BEFORE any of them can
    execute. Returns counters for the round receipt."""
    data = plan.data or {}
    plan_identity = str(data.get("plan_identity") or "")
    frontier = _frontier_rows(graph, plan_identity)
    known = {_norm(obj.data.get("text") or "") for obj in frontier}
    prior_texts = [str(obj.data.get("text") or "") for obj in frontier]
    recorded = paused = rejected = 0
    budget = min(int(config["max_follow_ups_per_round"]), max(0, remaining_query_budget))
    for suggestion in suggestions:
        if recorded + paused >= budget:
            break
        text = str(suggestion.get("text") or "").strip()
        if not text or _norm(text) in known:
            continue
        known.add(_norm(text))
        gate = scope_gate_for_query(
            text,
            scope_terms=config["scope_terms"],
            prior_query_texts=prior_texts,
            exclusions=config["exclusions"],
            sensitive_terms=config["sensitive_topic_terms"],
        )
        if gate["verdict"] == "rejected":
            status = "blocked_scope"
            rejected += 1
        elif gate["verdict"] == "amendment" or not config["auto_follow_up"]:
            status = "needs_approval"
            paused += 1
        else:
            status = "approved_auto"
            recorded += 1
        query = graph.add_object("research_query", {
            "query_identity": _stable("research_query", plan_identity, _norm(text)),
            "plan_identity": plan_identity,
            "run_ref": run_ref,
            "text": text,
            "origin": "follow_up",
            "parent_query_id": suggestion.get("parent_query_id"),
            "motivated_by": list(suggestion.get("motivated_by") or [])[:5],
            "scope_entity": "owner",
            "round": next_round,
            "expected_gain": str(suggestion.get("expected_gain") or "model-suggested deepening"),
            "status": status,
            "metadata": {"gate": gate} if gate["verdict"] != "auto" else {},
        })
        if status == "needs_approval":
            reason_kind = gate["reason_kind"] or "new_entity"
            graph.add_object("research_scope_amendment", {
                "amendment_identity": _stable(
                    "research_amendment", plan_identity, _norm(text)
                ),
                "plan_identity": plan_identity,
                "query_id": query.id,
                "query_text": text,
                "reason_kind": reason_kind if gate["verdict"] == "amendment" else "new_entity",
                "reason_detail": gate["reason_detail"] or "follow-up requires explicit approval",
            })
    return {"recorded": recorded, "paused": paused, "rejected": rejected}


def record_coordinator_query_fn(
    graph, *, text: str, expected_gain: str = "", reader=None,
) -> dict[str, Any]:
    """Record ONE coordinator-proposed query into the running campaign's
    frontier (ADR 0047 §2). Pre-registration rules are identical to model
    follow-ups: the scope gate classifies it, an amendment pauses it, and
    only an approved row ever executes — on the next pump round, never here.
    """
    view = reader or graph
    run = next(
        (obj for obj in view.objects(type="web_research_run")
         if obj.data.get("status") == "running"),
        None,
    )
    if run is None:
        return {"ok": False, "reason": "no_running_campaign"}
    data = run.data or {}
    plan = _plan_for_run(view, data)
    if plan is None:
        return {"ok": False, "reason": "plan_not_found"}
    config = campaign_config(plan.data or {})
    executed = sum(
        1 for obj in _frontier_rows(view, str(data.get("plan_identity") or ""))
        if obj.data.get("status") in ("executed", "no_results", "failed")
    )
    counts = record_follow_up_queries_fn(
        graph, plan, run.id,
        suggestions=[{
            "text": text,
            "expected_gain": expected_gain or "coordinator-proposed deepening",
        }],
        next_round=int(data.get("rounds_executed") or 0) + 1,
        config={**config, "max_follow_ups_per_round": 1, "auto_follow_up": True},
        remaining_query_budget=max(0, config["max_total_queries"] - executed),
    )
    if counts["rejected"]:
        return {"ok": False, "reason": "scope_rejected", **counts}
    if counts["paused"]:
        return {"ok": True, "paused_as_amendment": True, **counts}
    return {"ok": counts["recorded"] > 0, **counts}


def review_scope_amendment_fn(
    graph, amendment_ref: str, verdict: str, *, actor: str = "owner", reader=None
) -> dict[str, Any]:
    """Owner verdict on a paused query. Approval re-admits it to the
    frontier for the next executed round; decline is durable."""
    view = reader or graph
    amendment = None
    getter = getattr(view, "get_object", None)
    if callable(getter):
        try:
            candidate = getter(amendment_ref)
        except Exception:
            candidate = None
        if candidate is not None and getattr(candidate, "type", None) == "research_scope_amendment":
            amendment = candidate
    if amendment is None:
        amendment = next(
            (obj for obj in view.objects(type="research_scope_amendment")
             if obj.data.get("amendment_identity") == amendment_ref),
            None,
        )
    if amendment is None:
        return {"ok": False, "reason": "amendment_not_found"}
    if amendment.data.get("status") != "proposed":
        return {"ok": False, "reason": "already_decided",
                "status": amendment.data.get("status")}
    if verdict not in ("approve", "decline"):
        raise ValueError("verdict must be approve or decline")
    status = "approved" if verdict == "approve" else "declined"
    graph.patch_object(amendment.id, {"status": status, "decided_by": actor})
    query_id = str(amendment.data.get("query_id") or "")
    query = graph.get_object(query_id) if query_id else None
    if query is not None:
        graph.patch_object(query.id, {
            "status": "approved_auto" if verdict == "approve" else "declined",
        })
    return {"ok": True, "status": status, "query_id": query_id}


# ---- round execution --------------------------------------------------------

def _run_by_ref(reader, run_ref: str):
    getter = getattr(reader, "get_object", None)
    if callable(getter):
        try:
            obj = getter(run_ref)
        except Exception:
            obj = None
        if obj is not None and getattr(obj, "type", None) == "web_research_run":
            return obj
    return next(
        (obj for obj in reader.objects(type="web_research_run")
         if obj.data.get("run_identity") == run_ref),
        None,
    )


def _plan_for_run(reader, run_data: dict[str, Any]):
    from packs.connector_control.plans import resolve_plan_fn

    return resolve_plan_fn(reader, str(run_data.get("plan_identity") or ""))


def pending_research_rounds_fn(reader) -> list[dict[str, Any]]:
    """Running campaigns with approved, unexecuted frontier queries — the
    host pump polls this for round continuations (rounds ≥ 2)."""
    rows: list[dict[str, Any]] = []
    for run in reader.objects(type="web_research_run"):
        data = run.data or {}
        if data.get("status") != "running":
            continue
        if int(data.get("rounds_executed") or 0) < 1:
            continue  # round 1 rides the deferred plan seam
        plan_identity = str(data.get("plan_identity") or "")
        pending = [
            obj for obj in _frontier_rows(reader, plan_identity)
            if obj.data.get("status") == "approved_auto"
        ]
        if not pending:
            continue
        rows.append({
            "run_ref": run.id,
            "plan_identity": plan_identity,
            "next_round": int(data.get("rounds_executed") or 0) + 1,
            "pending_queries": len(pending),
        })
    rows.sort(key=lambda row: row["plan_identity"])
    return rows


def begin_research_round_fn(graph, run_ref: str, *, reader=None) -> dict[str, Any]:
    """Engine-thread phase 1 for a continuation round: reads only."""
    view = reader or graph
    run = _run_by_ref(view, run_ref)
    if run is None:
        return {"ok": False, "reason": "run_not_found"}
    data = run.data or {}
    if data.get("status") != "running":
        return {"ok": False, "reason": "run_not_running", "status": data.get("status")}
    plan = _plan_for_run(view, data)
    if plan is None:
        return {"ok": False, "reason": "plan_not_found"}
    config = campaign_config(plan.data or {})
    executed = sum(
        1 for obj in _frontier_rows(view, str(data.get("plan_identity") or ""))
        if obj.data.get("status") in ("executed", "no_results", "failed")
    )
    budget_left = max(0, config["max_total_queries"] - executed)
    queries = [
        {"query_id": obj.id, "text": str(obj.data.get("text") or "")}
        for obj in _frontier_rows(view, str(data.get("plan_identity") or ""))
        if obj.data.get("status") == "approved_auto"
    ][:budget_left]
    if not queries:
        return {"ok": False, "reason": "no_pending_queries"}
    return {
        "ok": True,
        "run_ref": run.id,
        "payload": {
            "round": int(data.get("rounds_executed") or 0) + 1,
            "queries": queries,
            "max_findings_per_query": config["max_findings_per_query"],
            "max_follow_ups": config["max_follow_ups_per_round"],
            "timeout_seconds": config["timeout_seconds_per_call"],
            "max_tokens": config["max_tokens_per_call"],
        },
    }


def perform_research_round(payload: dict[str, Any]) -> dict[str, Any]:
    """Worker phase 2: network only, zero graph access. One neutral-adapter
    call per recorded query; one query's failure stays one query's failure."""
    from packs.llm_provider import (
        configured_llm_provider, default_model_for, get_llm_provider,
    )

    resolved = configured_llm_provider()
    provider = get_llm_provider() if resolved.configured else None
    model = default_model_for(resolved)
    results = []
    for row in payload.get("queries") or []:
        if isinstance(row, str):
            row = {"query_id": None, "text": row}
        request = SearchRequest(
            query=str(row.get("text") or ""),
            max_findings=int(payload.get("max_findings_per_query") or 5),
            max_follow_ups=int(payload.get("max_follow_ups") or 0),
            timeout_seconds=float(payload.get("timeout_seconds") or 90.0),
            max_tokens=int(payload.get("max_tokens") or 1_500),
            allow_follow_up_suggestions=int(payload.get("max_follow_ups") or 0) > 0,
        )
        outcome = perform_neutral_search(
            request,
            provider_kind=resolved.provider,
            provider=provider,
            model=model,
        )
        results.append({
            "query_id": row.get("query_id"),
            "query": str(row.get("text") or ""),
            **outcome.as_dict(),
        })
    return {
        "results": results,
        "provider_kind": resolved.provider,
        "model": model,
    }


def commit_research_round_fn(
    graph, run_ref: str, payload: dict[str, Any], outcome: dict[str, Any],
    *, reader=None,
) -> dict[str, Any]:
    """Engine-thread phase 3: record findings and per-query results, derive
    and pre-register follow-ups, evaluate the deterministic stopping rules,
    ingest new pages through the governed gateway, and settle when stopped."""
    from packs.importers.public_presence.tools import bootstrap_public_presence_fn

    view = reader or graph
    run = _run_by_ref(view, run_ref)
    if run is None:
        return {"ok": False, "reason": "run_not_found"}
    data = dict(run.data or {})
    if data.get("status") != "running":
        return {"ok": True, "already_settled": True, "status": data.get("status")}
    plan = _plan_for_run(view, data)
    if plan is None:
        return {"ok": False, "reason": "plan_not_found"}
    plan_data = plan.data or {}
    plan_identity = str(plan_data.get("plan_identity") or "")
    config = campaign_config(plan_data)
    round_number = int(data.get("rounds_executed") or 0) + 1
    # Crash-recovery idempotency: a re-run commit for a round that already
    # landed must not double-append findings (ADR 0045 §6).
    committed_rounds = {
        int(row.get("round") or 0)
        for row in (data.get("metadata") or {}).get("rounds") or []
    }
    payload_round = payload.get("round")
    if payload_round is not None and int(payload_round) in committed_rounds:
        return {"ok": True, "already_committed": True, "round": int(payload_round)}

    # Owner abandonment or supersession mid-campaign fails closed.
    plan_open = plan_data.get("status") in ("approved", "executing")
    findings = list(data.get("findings") or [])
    seen_urls = {f.get("url") for f in findings if f.get("url")}
    executed_texts = list(data.get("queries") or [])
    calls = int(data.get("calls") or 0)
    new_urls: list[str] = []
    round_findings = 0
    provider_errors = 0
    unavailable = False
    suggestions: list[dict[str, Any]] = []
    model = data.get("model") or outcome.get("model")
    results = list(outcome.get("results") or [])
    responses = list((data.get("metadata") or {}).get("responses") or [])

    frontier_now = _frontier_rows(graph, plan_identity)
    for result in results:
        query_id = str(result.get("query_id") or "")
        query_obj = graph.get_object(query_id) if query_id else None
        if query_obj is None:
            wanted = _norm(str(result.get("query") or ""))
            query_obj = next(
                (obj for obj in frontier_now
                 if _norm(obj.data.get("text") or "") == wanted
                 and obj.data.get("status") == "approved_auto"),
                None,
            )
        query_text = (
            str(query_obj.data.get("text")) if query_obj is not None
            else str(result.get("query") or "")
        )
        ok = bool(result.get("ok"))
        rows = list(result.get("findings") or [])
        if ok:
            calls += 1
        else:
            provider_errors += 1
            if result.get("error") == "research_provider_unavailable":
                unavailable = True
        finding_refs: list[str] = []
        for row in rows:
            url = str(row.get("url") or "")
            if not _URL_RE.match(url):
                continue
            finding = {
                "claim": str(row.get("claim") or "")[:500],
                "url": url,
                "query": query_text,
                "round": round_number,
            }
            if row.get("injection_flags"):
                finding["injection_flags"] = list(row["injection_flags"])
            findings.append(finding)
            finding_refs.append(url)
            round_findings += 1
            if url not in seen_urls:
                seen_urls.add(url)
                new_urls.append(url)
        responses.append({
            "query": query_text,
            "round": round_number,
            "length": int(result.get("response_length") or 0),
            "parsed_findings": len(rows),
            "sample": str(result.get("response_sample") or "")[:400],
            "finish_reason": str(result.get("finish_reason") or ""),
            "error": result.get("error"),
        })
        if query_obj is not None:
            status = (
                "failed" if not ok else ("executed" if rows else "no_results")
            )
            graph.patch_object(query_obj.id, {
                "status": status,
                "run_ref": run.id,
                "result": {
                    "findings": len(rows),
                    "error": result.get("error"),
                    "recommend_continue": result.get("recommend_continue"),
                },
                "injection_flags": list(result.get("injection_flags") or []),
            })
        if query_text:
            executed_texts.append(query_text)
        # Follow-up suggestions derive from sanitized structured findings
        # only; a clean suggestion motivated by flagged findings is still
        # recorded but the flags travel with its lineage rows.
        for suggested in result.get("suggested_queries") or []:
            suggestions.append({
                "text": suggested,
                "parent_query_id": query_obj.id if query_obj is not None else None,
                "motivated_by": finding_refs[:5],
                "expected_gain": "model-suggested deepening from recorded findings",
            })

    executed_total = sum(
        1 for obj in _frontier_rows(graph, plan_identity)
        if obj.data.get("status") in ("executed", "no_results", "failed")
    )
    pages_budget_left = max(0, config["max_pages"] - int(data.get("urls_ingested") or 0))
    ingest_urls = new_urls[:pages_budget_left]
    ingested = 0
    if ingest_urls and plan_open:
        presence = bootstrap_public_presence_fn(
            graph,
            {"urls": ingest_urls},
            source_surface_id=str(data.get("source_surface_id") or "web_research:owner"),
            budget=len(ingest_urls),
            requested_by="web_research.campaign",
        )
        ingested = int(presence.get("proposed_calls") or 0)
    urls_ingested = int(data.get("urls_ingested") or 0) + ingested
    urls_planned = list(dict.fromkeys(list(data.get("urls_planned") or []) + ingest_urls))

    follow_up_counts = {"recorded": 0, "paused": 0, "rejected": 0}
    remaining_query_budget = max(0, config["max_total_queries"] - executed_total)
    if plan_open and suggestions and remaining_query_budget > 0:
        follow_up_counts = record_follow_up_queries_fn(
            graph, plan, run.id,
            suggestions=suggestions,
            next_round=round_number + 1,
            config=config,
            remaining_query_budget=remaining_query_budget,
        )

    pending_next = [
        obj for obj in _frontier_rows(graph, plan_identity)
        if obj.data.get("status") == "approved_auto"
    ]
    model_recommends = [
        result.get("recommend_continue") for result in results
        if isinstance(result.get("recommend_continue"), bool)
    ]

    # Deterministic stopping rules — authoritative over any recommendation.
    stop_reason: Optional[str] = None
    if not plan_open:
        stop_reason = "owner_abandoned"
    elif unavailable and round_findings == 0 and not findings:
        stop_reason = "provider_unavailable"
    elif provider_errors and provider_errors == len(results) and round_findings == 0:
        stop_reason = "provider_failure"
    elif round_number >= config["max_rounds"]:
        stop_reason = "max_rounds_reached"
    elif executed_total >= config["max_total_queries"]:
        stop_reason = "query_budget_exhausted"
    elif urls_ingested >= config["max_pages"]:
        stop_reason = "page_budget_exhausted"
    elif len(new_urls) < config["min_new_urls_per_round"]:
        stop_reason = "no_new_findings"
    elif not pending_next:
        stop_reason = "frontier_exhausted"

    rounds = list((data.get("metadata") or {}).get("rounds") or [])
    rounds.append({
        "round": round_number,
        "queries": len(results),
        "findings": round_findings,
        "new_urls": len(new_urls),
        "pages_ingested": ingested,
        "follow_ups": follow_up_counts,
        "provider_errors": provider_errors,
        "model_recommended_continue": (
            any(model_recommends) if model_recommends else None
        ),
        "continued": stop_reason is None,
        "stop_check": stop_reason or "continue",
    })

    metadata = {
        **(data.get("metadata") or {}),
        "responses": responses[-12:],
        "rounds": rounds,
        "provider_kind": outcome.get("provider_kind"),
    }
    patch: dict[str, Any] = {
        "queries": executed_texts,
        "findings": findings,
        "urls_planned": urls_planned,
        "urls_ingested": urls_ingested,
        "model": model,
        "calls": calls,
        "rounds_executed": round_number,
        "metadata": metadata,
    }
    if stop_reason is None:
        graph.patch_object(run.id, patch, rationale="research round committed")
        return {
            "ok": True, "run_id": run.id, "round": round_number,
            "stopped": False, "pending_queries": len(pending_next),
        }

    hard_failed = stop_reason in ("provider_failure", "provider_unavailable") and not findings
    status = "failed" if hard_failed else ("completed" if findings else "partial")
    patch.update({
        "status": status,
        "stop_reason": stop_reason,
        "error": (
            "research_provider_unavailable" if stop_reason == "provider_unavailable"
            else ("provider errors on every query" if stop_reason == "provider_failure" and hard_failed else data.get("error"))
        ),
    })
    graph.patch_object(run.id, patch, rationale="web research settled")
    _settle_campaign(graph, plan, run.id, status=status, stop_reason=stop_reason,
                     findings=len(findings), urls_planned=urls_planned,
                     urls_ingested=urls_ingested, calls=calls,
                     queries_executed=executed_total)
    return {
        "ok": status != "failed", "run_id": run.id, "round": round_number,
        "stopped": True, "stop_reason": stop_reason, "status": status,
    }


def _settle_campaign(
    graph, plan, run_id: str, *, status: str, stop_reason: str,
    findings: int, urls_planned: list[str], urls_ingested: int, calls: int,
    queries_executed: int,
) -> None:
    """Terminal connector-control citizenship: observation (which settles the
    plan through the neutral behavior), learning delta with planned-vs-actual,
    and the documents-family native view. The stop reason is on the receipt."""
    from packs.connector_control.tools import (
        record_connector_learning_delta_fn,
        record_connector_native_view_fn,
        record_connector_run_observation_fn,
    )

    data = plan.data or {}
    plan_identity = str(data.get("plan_identity") or "")
    caps = dict(data.get("caps") or {})
    config = campaign_config(data)
    surface = str(data.get("source_surface_id") or "web_research:owner")
    state = {"completed": "succeeded", "partial": "partial", "failed": "failed"}[status]
    record_connector_run_observation_fn(
        graph,
        domain_run_id=run_id,
        source_surface_id=surface,
        service="web_research",
        account_ref="owner",
        family="documents",
        route="model_search",
        state=state,
        phase="served" if state == "succeeded" else state,
        mode="backfill",
        attempt=True,
        bounds={
            "max_items": int(caps.get("max_items") or 0),
            "max_pages": config["max_total_queries"],
        },
        counts={
            "findings": findings, "pages": urls_ingested, "calls": calls,
            "queries": queries_executed,
        },
        metadata={
            "plan_identity": plan_identity,
            "plan_version": int(data.get("version") or 0),
            "stop_reason": stop_reason,
        },
    )
    record_connector_learning_delta_fn(
        graph,
        domain_run_id=run_id,
        source_surface_id=surface,
        service="web_research",
        family="documents",
        status={"succeeded": "complete", "partial": "partial", "failed": "failed"}[state],
        evidence={"created": urls_ingested, "updated": 0, "deleted": 0},
        refs=[run_id],
        plan={
            "plan_identity": plan_identity,
            "plan_version": int(data.get("version") or 0),
            "planned": {
                "max_items": int(caps.get("max_items") or 0),
                "max_queries": config["max_total_queries"],
                "max_rounds": config["max_rounds"],
            },
            "actual": {
                "imported": urls_ingested, "queries": queries_executed,
                "stop_reason": stop_reason,
            },
            "within_bounds": urls_ingested <= int(caps.get("max_items") or 0)
            and queries_executed <= config["max_total_queries"],
        },
    )
    record_connector_native_view_fn(
        graph,
        source_surface_id=surface,
        service="web_research",
        family="documents",
        state="ready" if urls_planned else ("failed" if state == "failed" else "empty"),
        data={
            "items": [
                {"item_ref": url, "title": url, "kind": "page"}
                for url in urls_planned
            ],
            "total_count": len(urls_planned),
        },
        refs=[run_id],
    )


__all__ = [
    "STOP_REASONS",
    "begin_research_round_fn",
    "record_coordinator_query_fn",
    "campaign_config",
    "commit_research_round_fn",
    "pending_research_rounds_fn",
    "perform_research_round",
    "record_follow_up_queries_fn",
    "record_seed_queries_fn",
    "review_scope_amendment_fn",
    "scope_gate_for_query",
]
