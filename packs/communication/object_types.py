"""Communication Pack object and relation types — v0.1.

Channel-neutral communication primitives shared by all adapter packs (Chat, Email, SMS, Voice).

Design rule: Communication Pack owns the semantic layer. Channel packs (Chat, Email)
own the channel-specific translation. Domain packs respond to comm_message objects
regardless of which channel produced them.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from activegraph.packs import ObjectType, RelationType


# ================================================================ Schemas


class CommThread(BaseModel):
    """A channel-specific conversation thread.

    Threads are the container for related messages. A thread may span many
    turns in chat, or an email reply chain, or an SMS conversation.

    CommThread is created by thread_tracker on the first message in a new thread,
    and updated as subsequent messages arrive.
    """

    channel: str = Field(
        description=(
            "Communication channel: 'chat', 'email', 'sms', 'call', 'slack', 'api', etc."
        )
    )
    subject: Optional[str] = Field(
        default=None,
        description="Thread subject (required for email, optional for chat).",
    )
    participant_ids: list[str] = Field(
        default_factory=list,
        description="IDs of CommParticipant objects that are part of this thread.",
    )
    status: Literal["open", "closed", "archived"] = Field(
        default="open",
        description="Thread lifecycle status.",
    )
    created_at: str = Field(
        default="",
        description="ISO 8601 datetime when this thread was created.",
    )
    last_message_at: Optional[str] = Field(
        default=None,
        description="ISO 8601 datetime of the last message in the thread.",
    )
    message_count: int = Field(
        default=0,
        description="Running count of messages in this thread.",
    )
    frame_id: Optional[str] = Field(
        default=None,
        description="ID of the frame this thread belongs to.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class CommMessage(BaseModel):
    """A channel-neutral message in a communication thread.

    CommMessage is the universal unit of communication. All channel adapters
    (Chat, Email, SMS) translate incoming channel-specific inputs into CommMessage
    objects so that domain packs can process them uniformly.

    Direction: 'inbound' = received from outside; 'outbound' = sent by the assistant.
    """

    thread_id: Optional[str] = Field(
        default=None,
        description="ID of the CommThread this message belongs to.",
    )
    channel: str = Field(
        description="Communication channel: 'chat', 'email', 'sms', 'call', etc."
    )
    sender_ref: str = Field(
        default="",
        description="Opaque reference to the sender (principal ID, email address, user ref, etc.).",
    )
    content: str = Field(
        default="",
        description="The message content (plain text or markdown).",
    )
    intent_hint: Optional[str] = Field(
        default=None,
        description=(
            "Optional pre-computed intent hint from the adapter. "
            "If set, intent_detector uses this as a strong prior."
        ),
    )
    direction: Literal["inbound", "outbound"] = Field(
        default="inbound",
        description="'inbound' = received; 'outbound' = sent by assistant.",
    )
    source_id: Optional[str] = Field(
        default=None,
        description="ID of the Core Source object this CommMessage was derived from.",
    )
    created_at: str = Field(
        default="",
        description="ISO 8601 datetime when this message was created.",
    )
    frame_id: Optional[str] = Field(
        default=None,
        description="ID of the frame this message belongs to.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class CommIntent(BaseModel):
    """The classified intent of an inbound CommMessage.

    Created by intent_detector on comm_message.created.

    Intent enum covers the most common communication intents:
    - query: a question or information request
    - request: a task or action request ("please do X")
    - reply: a response to a prior message
    - notification: informational, no action needed
    - review: something to evaluate/approve
    - approval_request: explicit request for permission/approval
    - unknown: intent could not be determined
    """

    message_id: str = Field(description="ID of the CommMessage this intent belongs to.")
    thread_id: Optional[str] = Field(
        default=None,
        description="ID of the CommThread (copied from the message for convenience).",
    )
    intent: Literal[
        "query", "request", "reply", "notification", "review", "approval_request", "unknown"
    ] = Field(
        default="unknown",
        description="Classified intent of the message.",
    )
    confidence: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Confidence score for the intent classification (0.0–1.0).",
    )
    reasoning: str = Field(
        default="",
        description="Short explanation of why this intent was chosen.",
    )
    secondary_intent: Optional[str] = Field(
        default=None,
        description="Secondary intent if the message has mixed intent.",
    )
    detected_by: str = Field(
        default="intent_detector",
        description="Name of the behavior that produced this intent.",
    )
    frame_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CommResponseCandidate(BaseModel):
    """A candidate response to an inbound message, pending approval or dispatch.

    Status lifecycle:
      draft     → created by an LLM or heuristic behavior, not yet reviewed
      proposed  → ready for review/approval
      approved  → approved (by owner or auto-approval policy)
      sent      → dispatched to the channel adapter and delivered
      rejected  → rejected (will not be sent)

    The response_dispatcher behavior fires on status='approved' and routes
    the candidate to the correct channel adapter for delivery.
    """

    message_id: str = Field(
        description="ID of the CommMessage this is a response to."
    )
    thread_id: Optional[str] = Field(
        default=None,
        description="ID of the CommThread (copied from the message for convenience).",
    )
    channel: str = Field(
        description="Channel this response should be delivered on."
    )
    content: str = Field(
        default="",
        description="The response content (plain text or markdown).",
    )
    artifact_id: Optional[str] = Field(
        default=None,
        description="ID of a Core Artifact if the response was derived from one.",
    )
    status: Literal["draft", "proposed", "approved", "sent", "rejected"] = Field(
        default="draft",
        description="Response lifecycle status.",
    )
    created_by_behavior: str = Field(
        default="",
        description="Name of the behavior that created this response candidate.",
    )
    rejection_reason: Optional[str] = Field(
        default=None,
        description="Reason if status='rejected'.",
    )
    frame_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CommParticipant(BaseModel):
    """A participant in a CommThread.

    CommParticipant is a graph node that links a principal_ref to a thread
    with a role. This allows querying "who is in this thread?" and
    "what role does this person play?" without embedding identity in CommThread.
    """

    thread_id: str = Field(description="ID of the CommThread this participant belongs to.")
    principal_ref: str = Field(
        description=(
            "Reference to the participant. Can be a principal ID, email address, "
            "user ref, or any opaque identifier."
        )
    )
    role: Literal["sender", "recipient", "cc", "observer"] = Field(
        default="recipient",
        description="Role in the thread: sender, recipient, cc, or observer.",
    )
    joined_at: str = Field(
        default="",
        description="ISO 8601 datetime when this participant was added to the thread.",
    )
    frame_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationThread(_StrictModel):
    thread_identity: str = Field(min_length=1)
    source_surface_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    account_ref: str = Field(min_length=1)
    provider_thread_id: str = Field(min_length=1)
    subject: str = ""
    participant_ids: list[str] = Field(default_factory=list)
    message_ids: list[str] = Field(default_factory=list)
    status: Literal["open", "closed", "archived"] = "open"
    last_message_at: Optional[str] = None
    unread_count: int = Field(default=0, ge=0)
    message_count: int = Field(default=0, ge=0)
    labels: list[str] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationMessage(_StrictModel):
    message_identity: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    source_surface_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    account_ref: str = Field(min_length=1)
    provider_message_id: str = Field(min_length=1)
    provider_revision_ref: str = Field(min_length=1)
    internet_message_id: Optional[str] = None
    sender: Optional[str] = None
    recipients: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    subject: str = ""
    sent_at: Optional[str] = None
    direction: Literal["inbound", "outbound", "unknown"] = "unknown"
    message_kind: Literal["human", "notification", "automated", "unknown"] = "unknown"
    labels: list[str] = Field(default_factory=list)
    unread: bool = False
    display_content: str = ""
    interpretation_content: str = ""
    interpretation_state: Literal["ready", "held", "suppressed", "empty"] = "empty"
    suppression_counts: dict[str, int] = Field(default_factory=dict)
    injection_flags: list[str] = Field(default_factory=list)
    evidence_id: str = Field(min_length=1)
    refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationParticipant(_StrictModel):
    participant_identity: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    address: str = Field(min_length=1)
    display_name: str = ""
    roles: list[Literal["sender", "recipient", "cc", "bcc", "observer"]] = Field(default_factory=list)
    entity_mention_id: Optional[str] = None
    entity_id: Optional[str] = None
    refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationSelection(_StrictModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    exact_hash: str = Field(min_length=64, max_length=64)


class ConversationInterpretationRun(_StrictModel):
    run_identity: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    evidence_revision_id: str = Field(min_length=1)
    selection_id: str = Field(min_length=1)
    deterministic_filter_version: str = Field(min_length=1)
    family_projector_version: str = Field(min_length=1)
    extraction_profile_id: Optional[str] = None
    extractor_refs: list[str] = Field(default_factory=list)
    requested_facets: list[str] = Field(default_factory=list)
    semantic_request_id: Optional[str] = None
    selections: list[ConversationSelection] = Field(default_factory=list)
    selected_chars: int = Field(default=0, ge=0)
    selected_content_hash: str = Field(min_length=64, max_length=64)
    status: Literal[
        "selected", "deterministic_only", "completed", "held", "suppressed",
        "failed", "invalidated",
    ] = "selected"
    semantic_run_ids: list[str] = Field(default_factory=list)
    annotation_ids: list[str] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    suppression_counts: dict[str, int] = Field(default_factory=dict)
    reprocess_of: Optional[str] = None
    error: Optional[str] = None
    refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ================================================================ ObjectType list

OBJECT_TYPES = [
    ObjectType(
        name="comm_thread",
        schema=CommThread,
        description=(
            "A channel-specific conversation thread. Container for related messages. "
            "Created by thread_tracker on first message in a new thread."
        ),
    ),
    ObjectType(
        name="comm_message",
        schema=CommMessage,
        description=(
            "A channel-neutral message. All adapters (Chat, Email) translate inputs "
            "into CommMessage so domain packs can process them uniformly."
        ),
    ),
    ObjectType(
        name="comm_intent",
        schema=CommIntent,
        description=(
            "Classified intent of an inbound CommMessage: query/request/reply/"
            "notification/review/approval_request/unknown. Created by intent_detector."
        ),
    ),
    ObjectType(
        name="comm_response_candidate",
        schema=CommResponseCandidate,
        description=(
            "Candidate response to an inbound message. Lifecycle: draft → proposed → "
            "approved → sent (or rejected). Dispatched by response_dispatcher on approved."
        ),
    ),
    ObjectType(
        name="comm_participant",
        schema=CommParticipant,
        description=(
            "A participant in a CommThread with a role (sender/recipient/cc/observer). "
            "Links principal_ref to thread without embedding identity in CommThread."
        ),
    ),
    ObjectType("conversation_thread", ConversationThread, "A durable connector-family conversation thread."),
    ObjectType("conversation_message", ConversationMessage, "A service-neutral message with deterministic hygiene and evidence provenance."),
    ObjectType("conversation_participant", ConversationParticipant, "A normalized participant in a conversation thread."),
    ObjectType("conversation_interpretation_run", ConversationInterpretationRun, "One pinned staged interpretation over evidence-selected segments."),
]


# ================================================================ RelationType list

RELATION_TYPES = [
    RelationType(
        name="thread_contains",
        source_types=("comm_thread",),
        target_types=("comm_message",),
        description="A CommThread contains a CommMessage.",
    ),
    RelationType(
        name="intent_of",
        source_types=("comm_intent",),
        target_types=("comm_message",),
        description="A CommIntent describes the intent of a CommMessage.",
    ),
    RelationType(
        name="response_to",
        source_types=("comm_response_candidate",),
        target_types=("comm_message",),
        description="A CommResponseCandidate is a response to a CommMessage.",
    ),
    RelationType(
        name="participates_in",
        source_types=("comm_participant",),
        target_types=("comm_thread",),
        description="A CommParticipant participates in a CommThread.",
    ),
    RelationType(
        name="derived_from_source_comm",
        source_types=("comm_message",),
        target_types=("source",),
        description="Alias kept for internal use. Use derived_from_source (Core) instead.",
    ),
    RelationType(
        name="dispatched_to",
        source_types=("comm_response_candidate",),
        target_types=("comm_thread",),
        description="A CommResponseCandidate was dispatched to a channel via its thread.",
    ),
    RelationType(
        name="delivers",
        # Source open (empty = any): the delivering object is a
        # capability_call, owned by the OPTIONAL Tool Gateway Pack. Channel
        # adapters (telegram, whatsapp, ...) create instances when they hand
        # an approved candidate to the gateway for external delivery.
        source_types=(),
        target_types=("comm_response_candidate",),
        description=(
            "A Tool Gateway capability_call delivers an approved response "
            "candidate to its external channel."
        ),
    ),
    RelationType(
        name="fulfills_intent",
        # Source left open (empty = any): the fulfilling object is a
        # capability_call, a type owned by the OPTIONAL Tool Gateway Pack —
        # this pack must not hard-couple its relation constraints to a type
        # it degrades gracefully without.
        source_types=(),
        target_types=("comm_intent",),
        description=(
            "An object (typically a Tool Gateway capability_call proposed by "
            "intent_router) fulfills a detected CommIntent."
        ),
    ),
    RelationType("conversation_contains", source_types=("conversation_thread",), target_types=("conversation_message",), description="A canonical conversation thread contains a message."),
    RelationType("conversation_has_participant", source_types=("conversation_thread",), target_types=("conversation_participant",), description="A canonical thread has a normalized participant."),
    RelationType("conversation_message_from_evidence", source_types=("conversation_message",), target_types=("activity_evidence",), description="A family message is grounded in one evidence revision."),
    RelationType("conversation_participant_mention", source_types=("conversation_participant",), target_types=("entity_mention",), description="A participant address is resolved through the entity pack."),
]
