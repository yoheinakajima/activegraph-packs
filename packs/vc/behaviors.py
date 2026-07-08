"""VC Pack behaviors — v0.1.

Behaviors:
  founder_email_detector  — comm_message.created → observation(kind=founder_outreach)
  company_enricher        — observation.created (founder_outreach) → CompanyProfile + FounderProfile
  memo_drafter            — company_profile.created → InvestmentMemo draft + Core Artifact
  followup_tracker        — company_profile.created → Followup item + Core Task
  lp_update_generator     — deal_round.created → LPUpdate draft

All LLM behaviors use deterministic mock stubs in v0.1 (no API key required).

Registries:
  _COMPANY_REGISTRY: sender_ref → company_profile_id
  _FOUNDER_REGISTRY: sender_ref → founder_profile_id
  Call clear_vc_registry() between test fixtures.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from activegraph.packs import behavior

from .settings import VCSettings

_COMPANY_REGISTRY: dict[str, str] = {}
_FOUNDER_REGISTRY: dict[str, str] = {}


def clear_vc_registry() -> None:
    _COMPANY_REGISTRY.clear()
    _FOUNDER_REGISTRY.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_founder_outreach(content: str, subject: str, keywords: list[str]) -> bool:
    """Return True if message content or subject suggests founder fundraise outreach."""
    text = (content + " " + subject).lower()
    return any(kw.lower() in text for kw in keywords)


def _extract_company_name(content: str, sender_ref: str) -> str:
    """Heuristically extract company name from email content or sender domain."""
    domain = sender_ref.split("@")[-1] if "@" in sender_ref else ""
    if domain and domain not in ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com"):
        company_guess = domain.split(".")[0].replace("-", " ").title()
    else:
        company_guess = "Unknown Company"

    cap_match = re.search(r"\b([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+){0,3})\b", content)
    if cap_match:
        candidate = cap_match.group(1)
        generic = {"Hi", "Hello", "Dear", "Thanks", "Thank", "Best", "Regards", "Sincerely", "We", "Our"}
        if candidate not in generic:
            return candidate

    return company_guess


def _extract_traction(content: str) -> dict:
    """Heuristically extract traction signals from email content."""
    traction = {}
    arr_match = re.search(r"\$?\s*(\d+(?:\.\d+)?)\s*[Mm](?:\s|$|[^a-z])\s*ARR", content, re.I)
    if arr_match:
        traction["ARR"] = float(arr_match.group(1)) * 1_000_000
    mrr_match = re.search(r"\$?\s*(\d+(?:\.\d+)?)[Kk]\s*MRR", content, re.I)
    if mrr_match:
        traction["MRR"] = float(mrr_match.group(1)) * 1_000
    growth_match = re.search(r"(\d+)%\s*(?:growth|MoM|YoY)", content, re.I)
    if growth_match:
        traction["growth_pct"] = float(growth_match.group(1))
    return traction


def _mock_memo_content(company_name: str, founder_name: str, summary: str) -> str:
    return f"""# Investment Memo: {company_name}

## Company Overview
{company_name} is an early-stage company reaching out for investment consideration.
Founder: {founder_name}

## Initial Signal Assessment
{summary}

## Key Questions for Diligence
- What is the current ARR and growth trajectory?
- What is the core competitive moat?
- Who are the key competitors and how does {company_name} differentiate?
- What does the founding team's background bring to this space?

## Preliminary Thesis
[To be completed after initial call and diligence phase]

## Next Steps
- Schedule founder call
- Request pitch deck and financials
- Research competitive landscape
"""


@behavior(
    name="founder_email_detector",
    on=["object.created"],
    where={"object.type": "comm_message"},
    creates=["observation"],
)
def founder_email_detector(event, graph, ctx, *, settings: VCSettings):
    """Detect founder fundraising outreach in inbound comm_messages.

    On: object.created (comm_message, channel=email or chat, direction=inbound)
    Creates: observation(category=intent, metadata.kind=founder_outreach)
    Relations: grounds(source → observation) if source_id available

    Detection: keyword matching on message content + subject.
    """
    obj = event.payload.get("object", {})
    msg_id = obj.get("id")
    data = obj.get("data", {})

    if data.get("direction") != "inbound":
        return

    content = data.get("content") or ""
    meta = data.get("metadata") or {}
    subject = meta.get("subject") or ""
    sender_ref = data.get("sender_ref") or ""
    source_id = data.get("source_id")

    if not _is_founder_outreach(content, subject, settings.founder_email_keywords):
        return

    try:
        obs = graph.add_object("observation", {
            "text": f"Potential founder outreach from '{sender_ref}': {subject or content[:80]}",
            "confidence": 0.78,
            "source_ids": [source_id] if source_id else [],
            "category": "intent",
            "metadata": {
                "kind": "founder_outreach",
                "sender_ref": sender_ref,
                "comm_message_id": msg_id,
                "subject": subject,
                "channel": data.get("channel"),
            },
        })
        if source_id:
            try:
                graph.add_relation(source_id, obs.id, "grounds")
            except Exception:
                pass
    except Exception:
        pass


@behavior(
    name="company_enricher",
    on=["object.created"],
    where={"object.type": "observation"},
    creates=["company_profile", "founder_profile"],
)
def company_enricher(event, graph, ctx, *, settings: VCSettings):
    """Create CompanyProfile + FounderProfile from founder outreach observations.

    On: object.created (observation, category=intent, metadata.kind=founder_outreach)
    Creates: company_profile, founder_profile
    Relations: founded_by, derived_from_comm

    v0.1: stub enrichment from email metadata (no external CRM API call).
    """
    obj = event.payload.get("object", {})
    data = obj.get("data", {})

    if data.get("category") != "intent":
        return
    meta = data.get("metadata") or {}
    if meta.get("kind") != "founder_outreach":
        return

    sender_ref = meta.get("sender_ref") or ""
    subject = meta.get("subject") or ""
    source_ids = data.get("source_ids") or []
    source_id = source_ids[0] if source_ids else None

    if sender_ref in _COMPANY_REGISTRY:
        return

    company_name = _extract_company_name(data.get("text") or "", sender_ref)

    try:
        company = graph.add_object("company_profile", {
            "name": company_name,
            "description": f"Company from founder outreach email (subject: '{subject}').",
            "stage": "unknown",
            "source_id": source_id,
        })
        _COMPANY_REGISTRY[sender_ref] = company.id
        if source_id:
            graph.add_relation(company.id, source_id, "derived_from_comm")
    except Exception:
        return

    if sender_ref in _FOUNDER_REGISTRY:
        founder_id = _FOUNDER_REGISTRY[sender_ref]
    else:
        try:
            founder = graph.add_object("founder_profile", {
                "name": sender_ref.split("@")[0].replace(".", " ").title(),
                "email": sender_ref if "@" in sender_ref else None,
                "company_id": _COMPANY_REGISTRY.get(sender_ref),
                "principal_ref": sender_ref,
            })
            _FOUNDER_REGISTRY[sender_ref] = founder.id
            founder_id = founder.id
            if source_id:
                graph.add_relation(founder.id, source_id, "founder_outreach_source")
        except Exception:
            founder_id = None

    if founder_id and sender_ref in _COMPANY_REGISTRY:
        try:
            graph.add_relation(_COMPANY_REGISTRY[sender_ref], founder_id, "founded_by")
        except Exception:
            pass


@behavior(
    name="memo_drafter",
    on=["object.created"],
    where={"object.type": "company_profile"},
    creates=["investment_memo", "artifact"],
)
def memo_drafter(event, graph, ctx, *, settings: VCSettings):
    """Draft a preliminary InvestmentMemo when a new CompanyProfile is created.

    On: object.created (company_profile)
    Creates: investment_memo (status=draft), artifact (kind=investment_note)
    Relations: memo_for(investment_memo → company_profile)

    v0.1: mock memo draft — template-based, no LLM call required.
    """
    if not settings.auto_draft_memo:
        return

    obj = event.payload.get("object", {})
    company_id = obj.get("id")
    data = obj.get("data", {})

    company_name = data.get("name") or "Unknown Company"

    sender_ref = None
    for ref, cid in _COMPANY_REGISTRY.items():
        if cid == company_id:
            sender_ref = ref
            break

    founder_name = (sender_ref or "").split("@")[0].replace(".", " ").title() if sender_ref else "Unknown Founder"
    content = _mock_memo_content(company_name, founder_name, data.get("description") or "")

    try:
        artifact = graph.add_object("artifact", {
            "kind": "investment_note",
            "title": f"Investment Memo: {company_name}",
            "content": content,
            "format": "markdown",
            "status": "draft",
        })
        memo_obj = graph.add_object("investment_memo", {
            "company_id": company_id,
            "title": f"Investment Memo: {company_name}",
            "content": content,
            "thesis_summary": f"Early-stage evaluation of {company_name}.",
            "status": "draft",
            "artifact_id": artifact.id,
        })
    except Exception:
        return

    try:
        graph.add_relation(memo_obj.id, company_id, "memo_for")
    except Exception:
        pass


@behavior(
    name="followup_tracker",
    on=["object.created"],
    where={"object.type": "company_profile"},
    creates=["followup", "task"],
)
def followup_tracker(event, graph, ctx, *, settings: VCSettings):
    """Create a Followup item and Core task when a new company is detected.

    On: object.created (company_profile)
    Creates: followup, task (Core)
    Relations: followup_for(followup → company_profile)
    """
    obj = event.payload.get("object", {})
    company_id = obj.get("id")
    data = obj.get("data", {})
    company_name = data.get("name") or "Unknown Company"

    from datetime import datetime, timezone, timedelta
    due = (datetime.now(timezone.utc) + timedelta(days=settings.followup_default_days)).isoformat()

    try:
        task = graph.add_object("task", {
            "title": f"Follow up with {company_name}",
            "description": f"Schedule intro call and review materials from {company_name}.",
            "status": "candidate",
            "priority": "medium",
            "due_at": due,
        })

        followup = graph.add_object("followup", {
            "company_id": company_id,
            "description": f"Schedule intro call and review materials.",
            "due_at": due,
            "status": "pending",
            "task_id": task.id,
        })
        graph.add_relation(followup.id, company_id, "followup_for")
    except Exception:
        pass


@behavior(
    name="lp_update_generator",
    on=["object.created"],
    where={"object.type": "deal_round"},
    creates=["lp_update", "artifact"],
)
def lp_update_generator(event, graph, ctx, *, settings: VCSettings):
    """Draft an LP update when a deal round status is notable.

    On: object.created (deal_round, status in {term_sheet, closing, closed})
    Creates: lp_update (draft), artifact (kind=report)

    v0.1: mock LP update generation.
    """
    obj = event.payload.get("object", {})
    round_id = obj.get("id")
    data = obj.get("data", {})

    status = data.get("status") or ""
    if status not in ("term_sheet", "closing", "closed"):
        return

    company_id = data.get("company_id") or ""
    round_type = data.get("round_type") or "round"
    committed = data.get("committed_amount") or 0
    firm = settings.owner_firm_name or "the fund"

    company_name = "Portfolio Company"
    if company_id in {v: v for v in _COMPANY_REGISTRY.values()}:
        pass
    for ref, cid in _COMPANY_REGISTRY.items():
        if cid == company_id:
            company_name = ref.split("@")[0].replace(".", " ").title()
            break

    content = (
        f"# LP Update — {company_name} {round_type.title()}\n\n"
        f"{firm} has reached '{status}' stage with {company_name} in their "
        f"{round_type} round"
        + (f" (${committed:,.0f} committed)" if committed else "")
        + ".\n\n"
        "## Summary\n"
        f"We are excited to share progress on our investment in {company_name}. "
        "Full details to follow upon closing.\n\n"
        "## Status\n"
        f"Deal status: **{status.replace('_', ' ').title()}**\n"
    )

    try:
        artifact = graph.add_object("artifact", {
            "kind": "report",
            "title": f"LP Update: {company_name} {round_type.title()}",
            "content": content,
            "format": "markdown",
            "status": "draft",
        })
        lp_update = graph.add_object("lp_update", {
            "title": f"LP Update: {company_name} {round_type.title()}",
            "content": content,
            "deal_ids": [round_id],
            "status": "draft",
            "artifact_id": artifact.id,
        })
    except Exception:
        pass


BEHAVIORS = [
    founder_email_detector,
    company_enricher,
    memo_drafter,
    followup_tracker,
    lp_update_generator,
]
