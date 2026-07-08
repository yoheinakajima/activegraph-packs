"""VC Pack tools — v0.1."""

from __future__ import annotations

from activegraph import Graph
from activegraph.packs import tool


def ingest_founder_email_fn(
    graph: Graph,
    sender_ref: str,
    content: str,
    subject: str = "",
    channel: str = "email",
) -> object:
    """Create a comm_message representing an inbound founder email."""
    source = graph.add_object("source", {
        "kind": "email",
        "content": content,
        "channel": channel,
        "sender_ref": sender_ref,
        "metadata": {"subject": subject},
    })
    msg = graph.add_object("comm_message", {
        "channel": channel,
        "sender_ref": sender_ref,
        "content": content,
        "direction": "inbound",
        "source_id": source.id,
        "metadata": {"subject": subject},
    })
    try:
        graph.add_relation(msg.id, source.id, "derived_from_source")
    except Exception:
        pass
    return msg


def create_deal_round_fn(
    graph: Graph,
    company_id: str,
    round_type: str = "seed",
    target_amount: float | None = None,
    committed_amount: float | None = None,
    status: str = "prospecting",
) -> object:
    """Create a DealRound for a company."""
    return graph.add_object("deal_round", {
        "company_id": company_id,
        "round_type": round_type,
        "target_amount": target_amount,
        "committed_amount": committed_amount,
        "status": status,
    })


def add_traction_metric_fn(
    graph: Graph,
    company_id: str,
    metric_name: str,
    value: float,
    unit: str = "USD",
    period: str | None = None,
    growth_rate: float | None = None,
) -> object:
    """Add a traction metric for a company."""
    metric = graph.add_object("traction_metric", {
        "company_id": company_id,
        "metric_name": metric_name,
        "value": value,
        "unit": unit,
        "period": period,
        "growth_rate": growth_rate,
    })
    try:
        graph.add_relation(company_id, metric.id, "reports_metric")
    except Exception:
        pass
    return metric


def add_deal_risk_fn(
    graph: Graph,
    company_id: str,
    risk_text: str,
    category: str = "other",
    severity: str = "medium",
    mitigation: str = "",
) -> object:
    """Add a DealRisk for a company."""
    risk = graph.add_object("deal_risk", {
        "company_id": company_id,
        "risk_text": risk_text,
        "category": category,
        "severity": severity,
        "mitigation": mitigation,
    })
    try:
        graph.add_relation(risk.id, company_id, "risk_in")
    except Exception:
        pass
    return risk


@tool(name="ingest_founder_email", description="Ingest a founder fundraising email as a comm_message.")
def ingest_founder_email(
    graph: Graph, sender_ref: str, content: str, subject: str = "", channel: str = "email"
) -> object:
    return ingest_founder_email_fn(graph, sender_ref, content, subject, channel)


@tool(name="create_deal_round", description="Create a DealRound for a company.")
def create_deal_round(
    graph: Graph, company_id: str, round_type: str = "seed",
    target_amount: float | None = None, committed_amount: float | None = None,
    status: str = "prospecting",
) -> object:
    return create_deal_round_fn(graph, company_id, round_type, target_amount, committed_amount, status)


@tool(name="add_traction_metric", description="Add a traction metric (ARR, MRR, DAU, etc.) to a company.")
def add_traction_metric(
    graph: Graph, company_id: str, metric_name: str, value: float,
    unit: str = "USD", period: str | None = None, growth_rate: float | None = None,
) -> object:
    return add_traction_metric_fn(graph, company_id, metric_name, value, unit, period, growth_rate)


@tool(name="add_deal_risk", description="Record a risk identified during deal evaluation.")
def add_deal_risk(
    graph: Graph, company_id: str, risk_text: str,
    category: str = "other", severity: str = "medium", mitigation: str = "",
) -> object:
    return add_deal_risk_fn(graph, company_id, risk_text, category, severity, mitigation)


TOOLS = [ingest_founder_email, create_deal_round, add_traction_metric, add_deal_risk]
