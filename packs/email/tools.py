"""Email Pack tools — v0.1."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from activegraph.packs import tool


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Plain implementation functions (callable from fixtures/tests) ─────────────


def ingest_email_fn(
    graph,
    message_id: str,
    from_addr: str,
    to_addrs: list,
    subject: str,
    body_text: str,
    cc_addrs: Optional[list] = None,
    thread_id: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[list] = None,
    received_at: Optional[str] = None,
    body_html: Optional[str] = None,
    headers: Optional[dict] = None,
):
    """Inject a raw email into the graph. Returns the EmailMessage object."""
    return graph.add_object("email_message", {
        "message_id": message_id,
        "from_addr": from_addr,
        "to_addrs": to_addrs,
        "cc_addrs": cc_addrs or [],
        "subject": subject,
        "body_text": body_text,
        "body_html": body_html,
        "thread_id": thread_id,
        "in_reply_to": in_reply_to,
        "references": references or [],
        "received_at": received_at or _now_iso(),
        "headers": headers or {},
    })


def create_email_response_fn(
    graph,
    comm_message_id: str,
    content: str,
    channel: str = "email",
    status: str = "draft",
    artifact_id: Optional[str] = None,
    thread_id: Optional[str] = None,
):
    """Create a CommResponseCandidate for an email, triggering reply_drafter + send_approver."""
    return graph.add_object("comm_response_candidate", {
        "message_id": comm_message_id,
        "thread_id": thread_id,
        "channel": channel,
        "content": content,
        "artifact_id": artifact_id,
        "status": status,
        "created_by_behavior": "manual",
    })


# ── Decorated @tool wrappers ──────────────────────────────────────────────────


@tool(
    name="ingest_email",
    description=(
        "Inject a raw email into the graph. Creates EmailMessage → triggers email_ingester → "
        "Source(kind=email) + CommMessage(channel=email) + EmailThread."
    ),
)
def ingest_email(
    graph,
    message_id: str,
    from_addr: str = "",
    to_addrs: list = None,
    subject: str = "",
    body_text: str = "",
    received_at: Optional[str] = None,
):
    return ingest_email_fn(
        graph, message_id=message_id, from_addr=from_addr,
        to_addrs=to_addrs, subject=subject, body_text=body_text,
        received_at=received_at,
    )


@tool(
    name="create_email_response",
    description=(
        "Create a CommResponseCandidate for an email. Triggers reply_drafter → "
        "EmailDraft, then send_approver → approval gate or auto-approve."
    ),
)
def create_email_response(
    graph,
    comm_message_id: str,
    content: str = "",
    status: str = "draft",
):
    return create_email_response_fn(
        graph, comm_message_id=comm_message_id,
        content=content, status=status,
    )


TOOLS = [ingest_email, create_email_response]
