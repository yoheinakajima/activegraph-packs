"""Telegram Adapter Pack behaviors — v0.1.

Two behaviors — the whole adapter:

  telegram_ingester    — telegram_update.created → chat_input. The Chat
                         Pack owns everything conversational (sessions,
                         memory, reply gating, the agentic responder);
                         this adapter only translates transport.

  telegram_dispatcher  — comm_response_candidate.created (channel=telegram,
                         approved) → capability_call proposal. Outbound
                         sends are ordinary gateway capabilities: recorded,
                         policy-checked, executed with the bot token
                         injected by the Secrets Pack at execution time.
                         The adapter never touches the network or the
                         credential itself.

Identity: user_ref is "telegram:<user_id>". Bind the owner with
IdentitySettings.owner_identifiers=["telegram:12345678"] (or
register_principal) so reply gating recognizes them.

Dedup: _SEEN_UPDATE_IDS is an in-process at-least-once guard for twitchy
pollers. Replay after a restart does not re-fire behaviors, so persisted
updates are never double-ingested; clear_seen_updates() between fixtures.
"""

from __future__ import annotations

from datetime import datetime, timezone

from activegraph.packs import behavior

from .settings import TelegramSettings

_SEEN_UPDATE_IDS: set[int] = set()


def clear_seen_updates() -> None:
    """Reset the in-process update dedup guard — call between fixtures."""
    _SEEN_UPDATE_IDS.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@behavior(
    name="telegram_ingester",
    on=["object.created"],
    where={"object.type": "telegram_update"},
    creates=["chat_input"],
)
def telegram_ingester(event, graph, ctx, *, settings: TelegramSettings):
    """Translate a Telegram update into a chat_input.

    On: object.created (telegram_update)
    Creates: chat_input (user_ref='telegram:<user_id>',
             metadata.channel='telegram', adapter routing data)
    Relations: ingested_as (telegram_update → chat_input)

    From chat_input onward the Chat Pack machinery runs unchanged: session
    continuity keys off user_ref, the reply gate consults the identity
    registry, and the responder answers on channel='telegram' so
    telegram_dispatcher delivers it.
    """
    obj = event.payload.get("object", {})
    update_id = obj.get("id")
    data = obj.get("data", {})

    tg_update_id = data.get("update_id")
    if tg_update_id in _SEEN_UPDATE_IDS:
        return
    if tg_update_id is not None:
        _SEEN_UPDATE_IDS.add(tg_update_id)

    text = (data.get("text") or "").strip()
    user_id = data.get("user_id")
    if not text or not user_id:
        return  # Non-text updates (stickers, joins) are recorded but not conversed.

    try:
        chat_input = graph.add_object("chat_input", {
            "user_ref": f"telegram:{user_id}",
            "content": text,
            "session_id": None,
            "frame_id": data.get("frame_id"),
            "role": "user",
            "metadata": {
                "channel": "telegram",
                "chat_id": data.get("chat_id"),
                "username": data.get("username"),
                "telegram_message_id": data.get("message_id"),
            },
        })
        # NOTE: add_relation signature is (source, target, type).
        graph.add_relation(update_id, chat_input.id, "ingested_as")
    except Exception:
        pass


@behavior(
    name="telegram_dispatcher",
    on=["object.created"],
    where={
        "object.type": "comm_response_candidate",
        "object.data.channel": "telegram",
        "object.data.status": "approved",
    },
    creates=["capability_call"],
)
def telegram_dispatcher(event, graph, ctx, *, settings: TelegramSettings):
    """Deliver an approved telegram response via the Tool Gateway.

    On: object.created (comm_response_candidate, channel=telegram, approved)
    Creates: capability_call (telegram.send_message, status=proposed)
    Relations: delivers (capability_call → comm_response_candidate)

    The adapter PROPOSES; the gateway records, policy-checks, injects the
    bot token, executes, sanitizes, and audits. Default risk class is 'low'
    (see TelegramSettings.outbound_risk_class): replying to the person who
    just messaged creates no new exposure — raise it to hold replies for
    manual approval.

    Routing: chat_id travels on the candidate (metadata.adapter, stamped by
    the responder from the inbound message) with a get_object fallback to
    the message itself; reminders should carry it in their payload metadata.
    """
    obj = event.payload.get("object", {})
    candidate_id = obj.get("id")
    data = obj.get("data", {})

    meta = data.get("metadata") or {}
    adapter = meta.get("adapter") or {}
    chat_id = adapter.get("chat_id") or meta.get("chat_id")

    if not chat_id and data.get("message_id"):
        try:
            msg = graph.get_object(data["message_id"])
            if msg is not None:
                chat_id = ((msg.data.get("metadata") or {}).get("adapter") or {}).get("chat_id")
        except Exception:
            pass
    if not chat_id:
        return  # No routing data — nothing to deliver to.

    content = data.get("content") or ""
    if not content:
        return

    try:
        call = graph.add_object("capability_call", {
            "provider_id": "",
            "provider_name": "telegram",
            "capability_name": "send_message",
            "input_data": {"chat_id": str(chat_id), "text": content},
            "credential_ref_name": settings.credential_name,
            "risk_class": settings.outbound_risk_class,
            "status": "proposed",
            "proposed_by": "telegram_dispatcher",
            "frame_id": data.get("frame_id"),
            "proposed_at": _now_iso(),
            "metadata": {"candidate_id": candidate_id},
        })
        # NOTE: add_relation signature is (source, target, type).
        graph.add_relation(call.id, candidate_id, "delivers")
        graph.patch_object(candidate_id, {"metadata": {**meta, "delivery_call_id": call.id}})
    except Exception:
        pass  # Tool Gateway not loaded — candidate stays undelivered but recorded.


BEHAVIORS = [telegram_ingester, telegram_dispatcher]
