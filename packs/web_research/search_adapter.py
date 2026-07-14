"""Provider-neutral search adapter (ADR 0045 §5).

One neutral request/result contract with explicit per-provider mappings.
Provider-specific wire syntax (Anthropic's ``web_search_20250305`` tool
block, OpenAI's ``web_search`` tool) lives HERE and nowhere else — a generic
interface never smuggles one provider's dialect to another. Unsupported
providers fail closed; zero-key resolution reports unavailable instead of
pretending. Everything the model returns is untrusted: parsed tolerantly,
injection-scanned, bounded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

SUPPORTED_SEARCH_PROVIDERS = ("anthropic", "openai")

_MAX_CLAIM_CHARS = 500
_MAX_QUERY_CHARS = 200
_MAX_SAMPLE_CHARS = 400


@dataclass(frozen=True)
class SearchRequest:
    """The neutral request: one query, bounded output."""

    query: str
    max_findings: int = 5
    max_follow_ups: int = 3
    timeout_seconds: float = 90.0
    max_tokens: int = 1_500
    allow_follow_up_suggestions: bool = True


@dataclass
class SearchOutcome:
    """The neutral result. ``ok`` is transport-level; zero findings with
    ``ok=True`` is an honest empty result, not an error."""

    ok: bool
    provider_kind: Optional[str]
    model: Optional[str]
    findings: list[dict[str, str]] = field(default_factory=list)
    suggested_queries: list[str] = field(default_factory=list)
    recommend_continue: Optional[bool] = None
    injection_flags: list[str] = field(default_factory=list)
    error: Optional[str] = None
    response_sample: str = ""
    response_length: int = 0
    finish_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "provider_kind": self.provider_kind,
            "model": self.model,
            "findings": list(self.findings),
            "suggested_queries": list(self.suggested_queries),
            "recommend_continue": self.recommend_continue,
            "injection_flags": list(self.injection_flags),
            "error": self.error,
            "response_sample": self.response_sample,
            "response_length": self.response_length,
            "finish_reason": self.finish_reason,
        }


def provider_search_tools(provider_kind: str) -> list[dict[str, Any]]:
    """The per-provider tool mapping — the ONLY place wire syntax lives."""
    kind = (provider_kind or "").strip().lower()
    if kind == "anthropic":
        return [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 3,
        }]
    if kind == "openai":
        return [{"type": "web_search"}]
    raise ValueError(f"search_provider_unsupported: {provider_kind!r}")


def _search_prompt(request: SearchRequest) -> tuple[str, str]:
    system = (
        "You research a person's public professional presence. Use web search. "
        "Only report claims directly supported by a page you actually found. "
        "Respond with STRICT JSON only, no prose."
    )
    follow_up_clause = (
        (
            f', "follow_up_queries": [up to {request.max_follow_ups} short search '
            'queries that would deepen coverage of the SAME person/organizations '
            'only], "continue": <true if more research would add value>'
        )
        if request.allow_follow_up_suggestions
        else ""
    )
    user = (
        f"Search the web for: {request.query}\n"
        f"Return at most {request.max_findings} findings as JSON: "
        '{"findings": [{"claim": "<one factual sentence>", "url": "<exact source url>"}]'
        f"{follow_up_clause}}}"
    )
    return system, user


def _parse_outcome(text: str, request: SearchRequest) -> tuple[
    list[dict[str, str]], list[str], Optional[bool], list[str]
]:
    """Tolerant parse + hardening: whole/fenced/balanced JSON, URL-validated
    findings, bounded strings, injection scan over everything model-authored."""
    from packs.llm_provider import parse_json_payload
    from packs.tool_gateway.untrusted import scan_for_injection

    payload = parse_json_payload(text) or {}
    findings: list[dict[str, str]] = []
    flags: list[str] = []
    for row in payload.get("findings") or []:
        claim = str((row or {}).get("claim") or "").strip()
        url = str((row or {}).get("url") or "").strip()
        if not claim or not _URL_RE.match(url):
            continue
        finding = {"claim": claim[:_MAX_CLAIM_CHARS], "url": url}
        claim_flags = scan_for_injection(claim)
        if claim_flags:
            # Flagged content stays inert evidence: recorded, never trusted
            # for derivation. The flag travels with the finding.
            finding["injection_flags"] = sorted(set(claim_flags))
            flags.extend(claim_flags)
        findings.append(finding)
        if len(findings) >= request.max_findings:
            break
    suggested: list[str] = []
    if request.allow_follow_up_suggestions:
        for row in payload.get("follow_up_queries") or []:
            query = str(row or "").strip()
            if not query:
                continue
            if scan_for_injection(query):
                flags.append("follow_up_query_injection")
                continue
            if query not in suggested:
                suggested.append(query[:_MAX_QUERY_CHARS])
            if len(suggested) >= request.max_follow_ups:
                break
    raw_continue = payload.get("continue")
    recommend = bool(raw_continue) if isinstance(raw_continue, bool) else None
    return findings, suggested, recommend, sorted(set(flags))


def perform_neutral_search(
    request: SearchRequest,
    *,
    provider_kind: Optional[str],
    provider,
    model: Optional[str],
) -> SearchOutcome:
    """Execute one search through the mapped provider. Network only — zero
    graph access (ADR 0041 perform phase)."""
    kind = (provider_kind or "").strip().lower() or None
    if provider is None or kind is None:
        return SearchOutcome(
            ok=False, provider_kind=kind, model=model,
            error="research_provider_unavailable",
        )
    try:
        tools = provider_search_tools(kind)
    except ValueError as exc:
        return SearchOutcome(
            ok=False, provider_kind=kind, model=model, error=str(exc),
        )
    from activegraph.llm import LLMMessage

    system, user = _search_prompt(request)
    try:
        response = provider.complete(
            system=system,
            messages=[LLMMessage(role="user", content=user)],
            model=model or "",
            max_tokens=request.max_tokens,
            temperature=0.0,
            top_p=1.0,
            output_schema=None,
            timeout_seconds=request.timeout_seconds,
            tools=tools,
        )
    except Exception as exc:
        return SearchOutcome(
            ok=False, provider_kind=kind, model=model,
            error=f"{type(exc).__name__}: {exc}"[:300],
        )
    from packs.llm_provider import response_finish_reason, response_text

    text = response_text(response)
    finish_reason = response_finish_reason(response)
    findings, suggested, recommend, flags = _parse_outcome(text, request)
    return SearchOutcome(
        ok=True,
        provider_kind=kind,
        model=model,
        findings=findings,
        suggested_queries=suggested,
        recommend_continue=recommend,
        injection_flags=flags,
        response_sample=text[:_MAX_SAMPLE_CHARS],
        response_length=len(text),
        finish_reason=finish_reason,
    )


__all__ = [
    "SUPPORTED_SEARCH_PROVIDERS",
    "SearchOutcome",
    "SearchRequest",
    "perform_neutral_search",
    "provider_search_tools",
]
