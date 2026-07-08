"""Communication Pack settings."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CommunicationSettings(BaseModel):
    """Settings for the Communication Pack.

    Controls intent detection, thread tracking, and response dispatch.
    """

    intent_detection_mode: str = Field(
        default="heuristic",
        description=(
            "How to detect intent. 'heuristic' uses keyword/pattern rules. "
            "'llm' delegates to an LLM behavior (requires LLM capability). "
            "Default: 'heuristic'."
        ),
    )
    auto_create_threads: bool = Field(
        default=True,
        description=(
            "When True, thread_tracker auto-creates CommThread objects for "
            "incoming messages that have no existing thread. Default: True."
        ),
    )
    default_channel: str = Field(
        default="chat",
        description="Default channel for messages without an explicit channel. Default: 'chat'.",
    )
    low_confidence_intent_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Intent confidence below this value is flagged as low-confidence "
            "and intent is set to 'unknown'. Default: 0.5."
        ),
    )
    intent_routes: dict[str, dict] = Field(
        default_factory=dict,
        description=(
            "Deterministic intent → Tool Gateway routing. Keys are intent kinds "
            "('request', 'approval_request', ...); values describe the capability "
            "call to propose: {'provider_name': ..., 'capability_name': ..., "
            "'risk_class': 'low'|'medium'|'high'|'critical' (default 'medium'), "
            "'input': {static kwargs}, 'content_field': name the message text is "
            "passed under (default 'text')}. Empty (default) = no routing — "
            "intents are informational only. Routed calls enter the normal "
            "gateway lifecycle (policy check, approval, execution, audit); this "
            "pack only PROPOSES. Requires the Tool Gateway Pack to act on the "
            "proposal; without it the capability_call object type does not exist "
            "and the router no-ops."
        ),
    )
    intent_route_min_confidence: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum detected-intent confidence for intent_router to propose a "
            "capability call. Low-confidence guesses must not trigger actions. "
            "Default: 0.6."
        ),
    )
    auto_dispatch_approved_responses: bool = Field(
        default=True,
        description=(
            "When True, response_dispatcher fires automatically on "
            "comm_response_candidate.status == approved. Default: True."
        ),
    )
    max_thread_participants: int = Field(
        default=50,
        description="Max number of participants tracked per thread. Default: 50.",
    )
