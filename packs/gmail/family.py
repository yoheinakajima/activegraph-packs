"""Gmail-owned mapping into the service-neutral conversation family."""

from __future__ import annotations

from email.utils import getaddresses
from typing import Any

from packs.communication.conversation import materialize_conversation_message_fn
from packs.communication.hygiene import select_conversation_content


MAPPER_VERSION = "gmail.conversation-mapper@0.1.0"


def _addresses(*values: Any) -> list[dict[str, str]]:
    parsed = getaddresses([str(value or "") for value in values])
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for name, address in parsed:
        normalized = address.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        rows.append({"display_name": name.strip(), "address": normalized})
    return rows


def _kind(metadata: dict[str, Any], sender: str | None) -> tuple[str, bool]:
    precedence = str(metadata.get("precedence") or "").lower()
    auto_submitted = str(metadata.get("auto_submitted") or "").lower()
    notification = bool(
        metadata.get("list_id")
        or metadata.get("list_unsubscribe")
        or precedence in {"bulk", "list", "junk"}
        or (auto_submitted and auto_submitted != "no")
    )
    automated = notification or bool(sender and any(
        marker in sender for marker in ("no-reply", "noreply", "notifications@")
    ))
    return ("notification" if notification else "automated" if automated else "human"), notification


def materialize_gmail_run_fn(
    graph, run, *, reader, reprocess: bool = False, offset: int = 0,
    max_items: int = 250,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Map every current evidence revision in one run at one batch boundary."""
    evidence = [
        obj for obj in reader.objects(type="activity_evidence")
        if obj.data.get("status") == "current"
        and (obj.data.get("normalized_metadata") or {}).get("connector_run_id") == run.id
    ]
    evidence.sort(key=lambda obj: (str(obj.data.get("provider_time") or ""), obj.id))
    evidence = evidence[offset:offset + max_items]
    state: dict[str, Any] = {
        "model_requests": sum(
            1 for obj in reader.objects(type="conversation_interpretation_run")
            if run.id in (obj.data.get("refs") or [])
            and obj.data.get("semantic_request_id")
        )
    }
    results = []
    account_ref = str(run.data.get("account_ref") or "").strip().lower()
    for item in evidence:
        metadata = dict(item.data.get("normalized_metadata") or {})
        provider_message_id = str(metadata.get("message_id") or item.data.get("provider_item_id") or "")
        if not provider_message_id:
            continue
        senders = _addresses(metadata.get("from"))
        recipients = _addresses(metadata.get("to"))
        cc = _addresses(metadata.get("cc"))
        bcc = _addresses(metadata.get("bcc"))
        sender = senders[0]["address"] if senders else None
        participant_rows: list[dict[str, Any]] = []
        for role, rows in (("sender", senders), ("recipient", recipients), ("cc", cc), ("bcc", bcc)):
            participant_rows.extend({**row, "roles": [role]} for row in rows)
        message_kind, notification = _kind(metadata, sender)
        labels = [str(value) for value in (metadata.get("labels") or [])]
        body_offset = int(metadata.get("body_offset") or 0)
        content = str(item.data.get("normalized_content") or "")
        body_chars = int(metadata.get("body_chars") or max(0, len(content) - body_offset))
        body = content[body_offset:body_offset + body_chars]
        hygiene = select_conversation_content(
            body,
            evidence_offset=body_offset,
            injection_flags=list(metadata.get("injection_flags") or []),
            notification=notification,
        )
        result = materialize_conversation_message_fn(
            graph,
            reader=reader,
            source_surface_id=str(item.data.get("source_surface_id") or ""),
            service="gmail",
            account_ref=account_ref,
            provider_thread_id=str(metadata.get("thread_id") or provider_message_id),
            provider_message_id=provider_message_id,
            provider_revision_ref=str(metadata.get("history_id") or item.data.get("content_hash") or "unknown"),
            evidence_id=item.id,
            evidence_revision_id=str(item.data.get("revision_id") or ""),
            subject=str(metadata.get("subject") or ""),
            sender=sender,
            recipients=[row["address"] for row in recipients],
            cc=[row["address"] for row in cc],
            sent_at=item.data.get("provider_time") or metadata.get("date"),
            direction=(
                "outbound" if sender == account_ref else
                "inbound" if account_ref in {row["address"] for row in [*recipients, *cc, *bcc]} else
                "unknown"
            ),
            message_kind=message_kind,
            labels=labels,
            unread="UNREAD" in labels,
            internet_message_id=metadata.get("message_id_header"),
            participants=participant_rows,
            hygiene=hygiene,
            refs=[run.id, item.id],
            state=state,
            reprocess=reprocess,
        )
        results.append(result)

    threads = state.get("threads") or {
        obj.data.get("thread_identity"): obj for obj in reader.objects(type="conversation_thread")
    }
    thread_data = state.get("thread_data") or {}
    participants = state.get("participants") or {
        obj.data.get("participant_identity"): obj for obj in reader.objects(type="conversation_participant")
    }
    participant_by_id = {obj.id: obj for obj in participants.values()}
    messages = state.get("messages") or {
        obj.data.get("message_identity"): obj for obj in reader.objects(type="conversation_message")
    }
    message_by_id = {obj.id: obj for obj in messages.values()}
    rows = []
    surface_id = str(run.data.get("source_surface_id") or "")
    for thread in threads.values():
        data = {**dict(thread.data), **dict(thread_data.get(thread.id) or {})}
        if data.get("source_surface_id") != surface_id:
            continue
        thread_messages = [
            message_by_id[mid]
            for mid in data.get("message_ids") or []
            if mid in message_by_id
        ]
        latest = max(
            thread_messages,
            key=lambda obj: (str(obj.data.get("sent_at") or ""), obj.id),
            default=None,
        )
        rows.append({
            "thread_ref": thread.id,
            "title": str(data.get("subject") or ""),
            "participant_refs": [
                participant_by_id[pid].id for pid in data.get("participant_ids") or []
                if pid in participant_by_id
            ],
            "last_message_at": data.get("last_message_at"),
            "unread_count": int(data.get("unread_count") or 0),
            "message_count": int(data.get("message_count") or 0),
            "latest_message_ref": latest.id if latest else None,
            "latest_sender": latest.data.get("sender") if latest else None,
            "preview": str(
                (latest.data.get("display_content") if latest else "") or ""
            )[:240],
            "interpretation_state": str(
                (latest.data.get("interpretation_state") if latest else "empty")
                or "empty"
            ),
            "status": str(data.get("status") or "open"),
            "refs": list(data.get("refs") or [])[:20],
        })
    rows.sort(key=lambda row: (str(row.get("last_message_at") or ""), row["thread_ref"]), reverse=True)
    native = {"threads": rows[:100], "total_count": len(rows)}
    return native, {
        "messages": len(results),
        "threads": len(rows),
        "semantic_requests": int(state.get("model_requests") or 0),
        "semantic_requests_created": sum(
            bool(result["interpretation_run"].data.get("semantic_request_id"))
            and result["interpretation_run"].data.get("status") == "selected"
            for result in results
        ),
        "mapper_version": MAPPER_VERSION,
    }


__all__ = ["MAPPER_VERSION", "materialize_gmail_run_fn"]
