"""WhatsApp Adapter Pack behaviors — v0.1.

Two behaviors — the whole adapter, structurally the mirror of the Telegram
Adapter Pack (the two differ only in wire shapes and identity refs):

  whatsapp_ingester    — whatsapp_message.created → chat_input. The Chat
                         Pack owns everything conversational.

  whatsapp_dispatcher  — comm_response_candidate.created (channel=whatsapp,
                         approved) → capability_call proposal. Outbound
                         sends are ordinary gateway capabilities with the
                         access token injected at execution time.

Identity: user_ref is "whatsapp:<phone>". Bind the owner with
IdentitySettings.owner_identifiers=["whatsapp:15551234567"] (or
register_principal) so reply gating recognizes them — a WhatsApp business
number is reachable by anyone who has it, which is exactly what the gate
is for.

Dedup: _SEEN_MESSAGE_IDS guards Meta's at-least-once webhook delivery
in-process; replay after restart does not re-fire behaviors.
"""

from __future__ import annotations

from datetime import datetime, timezone

from activegraph.packs import behavior

from .settings import WhatsAppSettings

_SEEN_MESSAGE_IDS: set[str] = set()


def clear_seen_messages() -> None:
    """Reset the in-process message dedup guard — call between fixtures."""
    _SEEN_MESSAGE_IDS.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@behavior(
    name="whatsapp_ingester",
    on=["object.created"],
    where={"object.type": "whatsapp_message"},
    creates=["chat_input"],
)
def whatsapp_ingester(event, graph, ctx, *, settings: WhatsAppSettings):
    """Translate a WhatsApp message into a chat_input.

    On: object.created (whatsapp_message)
    Creates: chat_input (user_ref='whatsapp:<phone>',
             metadata.channel='whatsapp', adapter routing data)
    Relations: wa_ingested_as (whatsapp_message → chat_input)

    From chat_input onward the Chat Pack machinery runs unchanged; the
    responder answers on channel='whatsapp' so whatsapp_dispatcher
    delivers it.
    """
    obj = event.payload.get("object", {})
    wa_obj_id = obj.get("id")
    data = obj.get("data", {})

    wamid = data.get("message_id")
    if wamid in _SEEN_MESSAGE_IDS:
        return
    if wamid:
        _SEEN_MESSAGE_IDS.add(wamid)

    text = (data.get("text") or "").strip()
    from_number = data.get("from_number")
    if not text or not from_number:
        return  # Non-text messages (media, reactions) are recorded but not conversed.

    try:
        chat_input = graph.add_object("chat_input", {
            "user_ref": f"whatsapp:{from_number}",
            "content": text,
            "session_id": None,
            "frame_id": data.get("frame_id"),
            "role": "user",
            "metadata": {
                "channel": "whatsapp",
                "to_number": from_number,
                "profile_name": data.get("profile_name"),
                "wamid": wamid,
            },
        })
        # NOTE: add_relation signature is (source, target, type).
        graph.add_relation(wa_obj_id, chat_input.id, "wa_ingested_as")
    except Exception:
        pass


@behavior(
    name="whatsapp_dispatcher",
    on=["object.created"],
    where={
        "object.type": "comm_response_candidate",
        "object.data.channel": "whatsapp",
        "object.data.status": "approved",
    },
    creates=["capability_call"],
)
def whatsapp_dispatcher(event, graph, ctx, *, settings: WhatsAppSettings):
    """Deliver an approved whatsapp response via the Tool Gateway.

    On: object.created (comm_response_candidate, channel=whatsapp, approved)
    Creates: capability_call (whatsapp.send_message, status=proposed)
    Relations: delivers (capability_call → comm_response_candidate)

    Mirrors telegram_dispatcher: the adapter PROPOSES, the gateway governs.
    Routing: the recipient number travels on the candidate
    (metadata.adapter.to_number) with a get_object fallback to the message.
    """
    obj = event.payload.get("object", {})
    candidate_id = obj.get("id")
    data = obj.get("data", {})

    meta = data.get("metadata") or {}
    adapter = meta.get("adapter") or {}
    to_number = adapter.get("to_number") or meta.get("to_number")

    if not to_number and data.get("message_id"):
        try:
            msg = graph.get_object(data["message_id"])
            if msg is not None:
                to_number = ((msg.data.get("metadata") or {}).get("adapter") or {}).get("to_number")
        except Exception:
            pass
    if not to_number:
        return

    content = data.get("content") or ""
    if not content:
        return

    try:
        call = graph.add_object("capability_call", {
            "provider_id": "",
            "provider_name": "whatsapp",
            "capability_name": "send_message",
            "input_data": {"to": str(to_number), "text": content},
            "credential_ref_name": settings.credential_name,
            "risk_class": settings.outbound_risk_class,
            "status": "proposed",
            "proposed_by": "whatsapp_dispatcher",
            "frame_id": data.get("frame_id"),
            "proposed_at": _now_iso(),
            "metadata": {"candidate_id": candidate_id},
        })
        # NOTE: add_relation signature is (source, target, type).
        graph.add_relation(call.id, candidate_id, "delivers")
        graph.patch_object(candidate_id, {"metadata": {**meta, "delivery_call_id": call.id}})
    except Exception:
        pass  # Tool Gateway not loaded — candidate stays undelivered but recorded.


BEHAVIORS = [whatsapp_ingester, whatsapp_dispatcher]
