"""Schedule Pack gateway capabilities.

Registers schedule creation as a Tool Gateway capability, which is what
makes "remind me tomorrow at 9am" a one-turn chat interaction: expose
``schedule.create_reminder`` in ``ChatSettings.tool_allow_list`` and the
LLM proposes it through the gateway — recorded, policy-checked, audited —
like any other capability.

The handler declares ``execution_context`` and receives the graph handle
from the gateway at execution time (the gateway is the mediator; no
construction-time closure needed), so registration is graph-free and can
happen at import/startup like any other capability.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class CreateReminderInput(BaseModel):
    """What the model supplies to schedule a reminder."""

    message: str = Field(description="The reminder text to deliver.")
    at: Optional[str] = Field(
        default=None,
        description="ISO 8601 datetime (UTC) for a one-shot reminder.",
    )
    every_seconds: Optional[int] = Field(
        default=None,
        ge=60,
        description="Period in seconds for a recurring reminder (min 60).",
    )
    channel: str = Field(
        default="chat",
        description="Channel to deliver on (chat, telegram, whatsapp, ...).",
    )
    thread_id: Optional[str] = Field(
        default=None,
        description="Existing comm thread to deliver into, if any.",
    )


def register_reminder_capability(*, risk_class: str = "low"):
    """Register ``schedule.create_reminder`` on the gateway.

    The reminder payload is a ``comm_response_candidate`` (status=approved):
    when the schedule fires, the channel's dispatcher delivers it exactly
    like any other approved outbound reply — reminders are ordinary
    messages that happen to originate from a tick.

    Action class R3, the deliberately-higher pick of a genuine R2/R3
    ambiguity: the schedule object itself is bounded and cancellable
    until it fires (R2-shaped), but the payload is a PRE-APPROVED
    outbound message that delivers on a real channel with no further
    gate when due — creating the reminder commits a future send, and a
    delivered message cannot be unsent. Ambiguity resolves upward per
    ADR 0016 discipline.

    Requires the Tool Gateway Pack (guarded import; returns None without it).
    """
    try:
        from packs.tool_gateway.tools import register_local_capability
    except Exception:
        return None

    def _create_reminder(
        message: str = "",
        at: Optional[str] = None,
        every_seconds: Optional[int] = None,
        channel: str = "chat",
        thread_id: Optional[str] = None,
        execution_context: Optional[dict] = None,
    ) -> dict:
        from datetime import datetime, timezone

        from .tools import create_schedule_fn

        graph = (execution_context or {}).get("graph")
        if graph is None:
            return {"ok": False, "reason": "no graph in execution_context — "
                                           "run through the Tool Gateway"}
        if not at and not every_seconds:
            return {"ok": False, "reason": "provide 'at' (once) or 'every_seconds' (recurring)"}

        kind = "once" if at else "interval"
        schedule = create_schedule_fn(
            graph,
            name=f"reminder: {message[:40]}",
            kind=kind,
            at=at,
            every_seconds=every_seconds,
            payload_emit_type="comm_response_candidate",
            payload_data={
                "message_id": "",
                "thread_id": thread_id,
                "channel": channel,
                "content": message,
                "status": "approved",
                "created_by_behavior": "schedule.tick_router",
                "metadata": {"reminder": True},
            },
            now=datetime.now(timezone.utc),
            created_by="schedule.create_reminder",
        )
        return {
            "ok": True,
            "schedule_id": schedule.id,
            "kind": kind,
            "next_due_at": schedule.data.get("next_due_at"),
        }

    return register_local_capability(
        "schedule", "create_reminder", _create_reminder,
        input_schema=CreateReminderInput,
        description=(
            "Schedule a reminder for the user: one-shot ('at', ISO 8601 UTC) "
            "or recurring ('every_seconds'). Delivered on the given channel "
            "when due."
        ),
        risk_class=risk_class,
        action_class="R3",
    )
