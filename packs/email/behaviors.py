"""Email Pack behaviors — v0.1.

Behaviors:
  email_ingester  — email_message.created → Source + CommMessage + EmailThread
  reply_drafter   — comm_response_candidate.created (channel=email) → EmailDraft
  send_approver   — email_draft.created → approval gate or auto-approve

Registries:
  _EMAIL_MESSAGE_REGISTRY: message_id → object_id (dedup by RFC 2822 Message-ID)
  _EMAIL_THREAD_REGISTRY: thread_key → {"email_thread_id", "comm_thread_id"}
  _EMAIL_TO_COMM_MESSAGE: email_message_id → comm_message_id
  _CANDIDATE_TO_DRAFT: response_candidate_id → email_draft_id
  Call clear_email_registry() between test fixtures.

Approval policy:
  External = recipient not in owner_email_addresses and not in trusted_domains.
  If external AND require_approval_for_external: creates Action(approval_request, risk_class=high).
  Internal: auto-approves draft + comm_response_candidate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from activegraph.packs import behavior

from .settings import EmailSettings

# ================================================================ Registries

_EMAIL_MESSAGE_REGISTRY: dict[str, str] = {}
_EMAIL_THREAD_REGISTRY: dict[str, dict] = {}
_EMAIL_TO_COMM_MESSAGE: dict[str, str] = {}
_CANDIDATE_TO_DRAFT: dict[str, str] = {}


def clear_email_registry() -> None:
    """Reset all email registries — call between test fixtures."""
    _EMAIL_MESSAGE_REGISTRY.clear()
    _EMAIL_THREAD_REGISTRY.clear()
    _EMAIL_TO_COMM_MESSAGE.clear()
    _CANDIDATE_TO_DRAFT.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_re_fwd(subject: str) -> str:
    """Strip Re:/Fwd:/Fw: prefixes from email subject."""
    cleaned = subject.strip()
    while True:
        lower = cleaned.lower()
        if lower.startswith("re:") or lower.startswith("fwd:") or lower.startswith("fw:"):
            cleaned = cleaned[cleaned.index(":") + 1:].strip()
        else:
            break
    return cleaned


def _is_internal(addr: str, owner_addrs: list[str], trusted_domains: list[str]) -> bool:
    """True if addr is an owner address or from a trusted domain."""
    if addr in owner_addrs:
        return True
    domain = addr.split("@")[-1].lower() if "@" in addr else ""
    return domain in [d.lower() for d in trusted_domains]


# ================================================================ Behaviors


@behavior(
    name="email_ingester",
    on=["object.created"],
    where={"object.type": "email_message"},
    creates=["source", "comm_message", "email_thread"],
)
def email_ingester(event, graph, ctx, *, settings: EmailSettings):
    """Translate an EmailMessage into Source + CommMessage + EmailThread.

    On: object.created (email_message)
    Creates: source(kind=email), comm_message(channel=email, inbound),
             email_thread (created/updated)
    Relations: email_thread_contains, derived_from_source

    Dedup: skips if message_id already in _EMAIL_MESSAGE_REGISTRY.
    Threading: uses In-Reply-To / thread_id to link to existing EmailThread.
    Hook: source.created triggers Identity Pack principal_resolver (if loaded).
    """
    obj = event.payload.get("object", {})
    email_obj_id = obj.get("id")
    em = obj.get("data", {})

    message_id = em.get("message_id") or email_obj_id
    now = _now_iso()

    # Dedup
    if message_id in _EMAIL_MESSAGE_REGISTRY:
        return
    _EMAIL_MESSAGE_REGISTRY[message_id] = email_obj_id

    from_addr = em.get("from_addr") or ""
    to_addrs = em.get("to_addrs") or []
    cc_addrs = em.get("cc_addrs") or []
    subject = em.get("subject") or ""
    body_text = (em.get("body_text") or "")[:settings.max_body_length]
    thread_id_hint = em.get("thread_id") or em.get("in_reply_to") or message_id
    received_at = em.get("received_at") or now

    # ── 1. Resolve or create EmailThread ──────────────────────────────────
    thread_key = thread_id_hint
    email_thread_id = None

    if thread_key in _EMAIL_THREAD_REGISTRY:
        reg = _EMAIL_THREAD_REGISTRY[thread_key]
        email_thread_id = reg["email_thread_id"]
        try:
            existing = graph.get_object(email_thread_id)
            if existing:
                current_addrs = existing.data.get("participant_addrs") or []
                new_addrs = list(set(current_addrs + [from_addr] + to_addrs + cc_addrs))
                new_count = existing.data.get("message_count", 0) + 1
                graph.patch_object(email_thread_id, {
                    "participant_addrs": new_addrs,
                    "message_count": new_count,
                    "last_message_at": received_at,
                })
        except Exception:
            pass

    elif settings.auto_create_threads:
        try:
            all_participants = list(set([from_addr] + to_addrs + cc_addrs))
            clean_subject = _strip_re_fwd(subject)
            email_thread = graph.add_object("email_thread", {
                "thread_id": thread_id_hint,
                "subject": clean_subject,
                "participant_addrs": all_participants,
                "message_count": 1,
                "last_message_at": received_at,
                "first_message_id": message_id,
            })
            email_thread_id = email_thread.id
            _EMAIL_THREAD_REGISTRY[thread_key] = {
                "email_thread_id": email_thread_id,
                "comm_thread_id": None,
            }
        except Exception:
            pass

    if email_thread_id:
        try:
            graph.add_relation(email_thread_id, email_obj_id, "email_thread_contains")
        except Exception:
            pass

    # ── 2. Create Core Source ──────────────────────────────────────────────
    try:
        source = graph.add_object("source", {
            "kind": "email",
            "content": body_text,
            "channel": "email",
            "sender_ref": from_addr,
            "metadata": {
                "message_id": message_id,
                "subject": subject,
                "to_addrs": to_addrs,
                "cc_addrs": cc_addrs,
                "thread_id": thread_id_hint,
                "received_at": received_at,
            },
        })
        source_id = source.id
    except Exception:
        return

    # ── 3. Create CommMessage ──────────────────────────────────────────────
    try:
        comm_msg = graph.add_object("comm_message", {
            "channel": "email",
            "sender_ref": from_addr,
            "content": body_text,
            "direction": "inbound",
            "source_id": source_id,
            "metadata": {
                "subject": subject,
                "thread_id_hint": thread_id_hint,
                "message_id": message_id,
                "to_addrs": to_addrs,
                "cc_addrs": cc_addrs,
            },
        })
        _EMAIL_TO_COMM_MESSAGE[email_obj_id] = comm_msg.id
        graph.add_relation(comm_msg.id, source_id, "derived_from_source")
    except Exception:
        pass


@behavior(
    name="reply_drafter",
    on=["object.created"],
    where={"object.type": "comm_response_candidate"},
    creates=["email_draft"],
)
def reply_drafter(event, graph, ctx, *, settings: EmailSettings):
    """Format a CommResponseCandidate as an EmailDraft.

    On: object.created (comm_response_candidate, channel=email)
    Creates: email_draft (subject=Re:..., to_addrs from original sender)
    Relations: draft_from_candidate

    Reads original CommMessage for sender address and subject.
    Appends EmailSettings.draft_signature if configured.
    """
    obj = event.payload.get("object", {})
    candidate_id = obj.get("id")
    cdata = obj.get("data", {})

    if cdata.get("channel") != "email":
        return

    message_id = cdata.get("message_id")
    content = cdata.get("content") or ""
    artifact_id = cdata.get("artifact_id")
    frame_id = cdata.get("frame_id")

    to_addrs = []
    subject = "Re: (no subject)"
    in_reply_to_message_id = None

    if message_id:
        try:
            orig_msg = graph.get_object(message_id)
            if orig_msg:
                sender = orig_msg.data.get("sender_ref") or ""
                if sender:
                    to_addrs = [sender]
                meta = orig_msg.data.get("metadata") or {}
                raw_subject = meta.get("subject") or ""
                if raw_subject:
                    subject = f"Re: {_strip_re_fwd(raw_subject)}"
                in_reply_to_message_id = meta.get("message_id")
        except Exception:
            pass

    body_parts = [content]
    if settings.draft_signature:
        body_parts.append(f"\n\n--\n{settings.draft_signature}")
    body = "\n".join(body_parts)

    try:
        draft = graph.add_object("email_draft", {
            "to_addrs": to_addrs,
            "cc_addrs": [],
            "subject": subject,
            "body": body,
            "in_reply_to_message_id": in_reply_to_message_id,
            "response_candidate_id": candidate_id,
            "artifact_id": artifact_id,
            "status": "draft",
            "requires_approval": settings.require_approval_for_external,
            "metadata": {
                "reply_to_comm_message_id": message_id,
                "thread_id": cdata.get("thread_id"),
            },
        })
        _CANDIDATE_TO_DRAFT[candidate_id] = draft.id
        graph.add_relation(draft.id, candidate_id, "draft_from_candidate")
    except Exception:
        pass


@behavior(
    name="send_approver",
    on=["object.created"],
    where={"object.type": "email_draft"},
    creates=["action"],
)
def send_approver(event, graph, ctx, *, settings: EmailSettings):
    """Gate outbound email sends with an approval policy.

    On: object.created (email_draft, status='draft')
    Policy: require_approval_for_external=True (default)

    If external AND require_approval: creates Action(kind=approval_request, risk_class=high).
      Draft stays in 'draft' status — owner must approve before delivery.
    If internal (owner/trusted domain) OR approval disabled: auto-approves draft.
      Also patches comm_response_candidate.status = 'approved'
      → triggers response_dispatcher (Communication Pack) → marks candidate 'sent'.
    """
    obj = event.payload.get("object", {})
    draft_id = obj.get("id")
    ddata = obj.get("data", {})

    if ddata.get("status") != "draft":
        return

    to_addrs = ddata.get("to_addrs") or []
    owner_addrs = settings.owner_email_addresses or []
    trusted_domains = settings.trusted_domains or []

    has_external = any(
        not _is_internal(addr, owner_addrs, trusted_domains)
        for addr in to_addrs
    ) if to_addrs else False

    needs_approval = settings.require_approval_for_external and (has_external or not to_addrs)

    if needs_approval:
        try:
            graph.add_object("action", {
                "kind": "approval_request",
                "description": (
                    f"Approve outbound email draft '{ddata.get('subject', '')}' "
                    f"to {', '.join(to_addrs) or '(no recipients)'}"
                ),
                "status": "proposed",
                "proposed_by": "send_approver",
                "risk_class": "high",
                "input_data": {
                    "email_draft_id": draft_id,
                    "to_addrs": to_addrs,
                    "subject": ddata.get("subject") or "",
                },
                "metadata": {"email_draft_id": draft_id},
            })
        except Exception:
            pass
    else:
        # Internal auto-approve: mark draft and candidate as "sent" directly.
        # Cannot use "approved" → patch → response_dispatcher chain because
        # patch_object does NOT re-trigger object.created behaviors.
        try:
            graph.patch_object(draft_id, {"status": "sent"})
        except Exception:
            pass

        response_candidate_id = ddata.get("response_candidate_id")
        if response_candidate_id:
            try:
                graph.patch_object(response_candidate_id, {"status": "sent"})
            except Exception:
                pass


# ================================================================ BEHAVIORS registry

BEHAVIORS = [email_ingester, reply_drafter, send_approver]
