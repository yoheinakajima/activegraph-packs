"""Core Pack object and relation types — v0.1.

Seven universal primitives that form the shared substrate for all domain packs.
These are intentionally minimal. Do NOT add domain-specific fields here.

Design rule: Core should be observation-first. Observations are weaker than
claims — they record "the system noticed this" without asserting truth.

Key invariant: person, company, claim, evidence, document do NOT belong here.
Those live in Entity Pack and domain packs.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from activegraph.packs import ObjectType, RelationType


# ================================================================ Schemas


class Source(BaseModel):
    """Something the system received or observed from the outside world.

    Examples: chat message, email, SMS, call transcript, uploaded file,
    URL, tool result, API response, repo file, meeting note.

    Source is the entry point — raw inputs become sources before anything
    else happens. Behaviors extract observations from sources.
    """

    kind: str = Field(
        description=(
            "Type of source. Suggested values: chat_message, email, sms, "
            "call_transcript, file, url, tool_result, api_response, "
            "repo_file, meeting_note, webhook."
        )
    )
    content: str = Field(
        default="",
        description="The raw content of the source (text, serialized JSON, etc.).",
    )
    url: Optional[str] = Field(
        default=None,
        description="URL or URI where the source originated, if applicable.",
    )
    channel: Optional[str] = Field(
        default=None,
        description="Communication channel (e.g. 'chat', 'email', 'sms', 'api').",
    )
    sender_ref: Optional[str] = Field(
        default=None,
        description="Opaque reference to the sender (principal ID, email address, etc.).",
    )
    frame_id: Optional[str] = Field(
        default=None,
        description="ID of the frame this source belongs to.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary key-value metadata (headers, timestamps, etc.).",
    )


class Observation(BaseModel):
    """A source-grounded thing the system noticed.

    Observations are weaker than claims. An observation says
    'the system noticed this in a source.' It does not assert truth.

    Examples:
    - 'User wants to build Core Pack first.'
    - 'Founder says ARR is $80k.'
    - 'Email asks for follow-up.'
    - 'Meeting included a decision.'

    Domain packs (Research, VC) may promote high-confidence observations
    to domain-specific claims — but that happens in those packs, not here.
    """

    text: str = Field(description="The observation text, written as a factual statement.")
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="How confident the system is in this observation (0.0–1.0).",
    )
    low_confidence: bool = Field(
        default=False,
        description="True if confidence is below the CoreSettings threshold.",
    )
    source_ids: list[str] = Field(
        default_factory=list,
        description="IDs of source objects that ground this observation.",
    )
    frame_id: Optional[str] = Field(
        default=None,
        description="ID of the frame this observation belongs to.",
    )
    category: Optional[str] = Field(
        default=None,
        description=(
            "Optional category tag. Suggested: intent, fact, decision, "
            "question, preference, action_item, risk, sentiment."
        ),
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class Task(BaseModel):
    """A minimal unit of work.

    Core task is deliberately underpowered. It is the universal work anchor
    that many packs can reference. Team/Ops Pack extends it with assignments,
    milestones, and workload.

    Do NOT add project-management fields here.
    """

    title: str = Field(description="Short title for the task (5–10 words).")
    description: str = Field(default="", description="Longer description of the work.")
    status: Literal["candidate", "active", "blocked", "done", "rejected"] = Field(
        default="candidate",
        description="Task lifecycle status.",
    )
    priority: Literal["low", "medium", "high", "critical"] = Field(
        default="medium",
        description="Task priority.",
    )
    source_observation_ids: list[str] = Field(
        default_factory=list,
        description="IDs of observations that motivated this task.",
    )
    owner_ref: Optional[str] = Field(
        default=None,
        description="Opaque reference to the task owner (principal ID, etc.).",
    )
    due_at: Optional[str] = Field(
        default=None,
        description="ISO 8601 datetime string for the task deadline.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class Action(BaseModel):
    """A proposed or executed operation.

    Actions are distinct from tasks:
    - Task: work to be done (goal-level)
    - Action: specific proposed/executed operation (execution-level)

    Examples: tool call, API call, MCP call, workflow run, graph patch,
    external write, send email, create GitHub issue, run code.

    Actions flow through Tool Gateway before execution. The model proposes;
    the runtime authorizes and executes.
    """

    kind: str = Field(
        description=(
            "Type of action. Suggested: tool_call, api_call, mcp_call, "
            "workflow_run, graph_patch, external_write, send_message, "
            "create_issue, run_code."
        )
    )
    description: str = Field(
        description="Human-readable description of what this action will do."
    )
    status: Literal["proposed", "authorized", "executing", "done", "failed", "rejected"] = Field(
        default="proposed",
        description="Action lifecycle status.",
    )
    proposed_by: Optional[str] = Field(
        default=None,
        description="Name of the behavior that proposed this action.",
    )
    input_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Input parameters for the action (no secrets).",
    )
    result: Optional[str] = Field(
        default=None,
        description="Serialized result after execution.",
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if the action failed.",
    )
    executed_at: Optional[str] = Field(
        default=None,
        description="ISO 8601 datetime of execution.",
    )
    credential_ref: Optional[str] = Field(
        default=None,
        description="Reference to the credential used (never the actual secret).",
    )
    frame_id: Optional[str] = Field(
        default=None,
        description="ID of the frame this action belongs to.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Arbitrary key-value metadata. Can carry 'principal_id' for "
            "permission_checker, audit context, etc."
        ),
    )
    risk_class: Literal["low", "medium", "high", "critical"] = Field(
        default="medium",
        description="Risk classification for policy/approval gating.",
    )


class Artifact(BaseModel):
    """A durable output produced by the assistant.

    Examples: memo, draft, report, email draft, code patch,
    slide deck, research note, investment note.

    Artifacts are the assistant's deliverables. They should always
    have source and task provenance so their origin is auditable.
    """

    kind: str = Field(
        description=(
            "Type of artifact. Suggested: memo, draft, report, email_draft, "
            "code_patch, slide_deck, research_note, investment_note, summary."
        )
    )
    title: str = Field(description="Short title for the artifact.")
    content: str = Field(
        default="",
        description="The artifact content (markdown, plain text, JSON string, etc.).",
    )
    format: str = Field(
        default="markdown",
        description="Content format: markdown, plain_text, json, html.",
    )
    source_ids: list[str] = Field(
        default_factory=list,
        description="IDs of source objects this artifact was derived from.",
    )
    task_ids: list[str] = Field(
        default_factory=list,
        description="IDs of tasks this artifact fulfills.",
    )
    observation_ids: list[str] = Field(
        default_factory=list,
        description="IDs of observations that contributed to this artifact.",
    )
    status: Literal["draft", "proposed", "approved", "rejected", "published"] = Field(
        default="draft",
        description="Artifact lifecycle status.",
    )
    frame_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryCandidate(BaseModel):
    """Something that might be worth remembering.

    Core only provides the candidate primitive. Memory Gateway Pack
    handles acceptance, storage, retrieval, ranking, and sync.

    Design rule: never write memory directly. Always create a candidate
    and let Memory Gateway decide whether to accept it.
    """

    text: str = Field(
        description="The memory text — what should be remembered, as a statement."
    )
    confidence: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="How confident the system is that this is worth remembering.",
    )
    source_ids: list[str] = Field(
        default_factory=list,
        description="IDs of sources that support this memory.",
    )
    observation_ids: list[str] = Field(
        default_factory=list,
        description="IDs of observations that led to this memory candidate.",
    )
    category: Optional[str] = Field(
        default=None,
        description=(
            "Memory category. Suggested: preference, relationship, decision, "
            "fact, instruction, style, context."
        ),
    )
    subject_ref: Optional[str] = Field(
        default=None,
        description="Who or what this memory is about (entity ID, principal ref, etc.).",
    )
    accepted: bool = Field(
        default=False,
        description="Set to True by Memory Gateway after evaluation.",
    )
    evaluation_id: Optional[str] = Field(
        default=None,
        description="ID of the Evaluation object that accepted/rejected this candidate.",
    )
    frame_id: Optional[str] = Field(default=None)


class Evaluation(BaseModel):
    """A judgment about an object, action, task, artifact, or memory candidate.

    Examples:
    - completed_successfully
    - low_confidence
    - requires_review
    - not_relevant
    - contradicted
    - high_priority
    - accepted (for memory candidates)
    - rejected
    """

    subject_id: str = Field(description="ID of the object being evaluated.")
    subject_type: str = Field(
        description=(
            "Type of the subject object: source, observation, task, action, "
            "artifact, memory_candidate, or a domain type."
        )
    )
    judgment: str = Field(
        description=(
            "The evaluation judgment. Suggested: completed_successfully, "
            "low_confidence, requires_review, not_relevant, contradicted, "
            "high_priority, accepted, rejected, needs_revision."
        )
    )
    rationale: str = Field(
        default="",
        description="Explanation of the judgment.",
    )
    evaluator: Optional[str] = Field(
        default=None,
        description="Name of the behavior or principal that made this evaluation.",
    )
    score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional numeric score (0.0–1.0) supporting the judgment.",
    )
    frame_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ================================================================ ObjectType list

OBJECT_TYPES = [
    ObjectType(
        name="source",
        schema=Source,
        description=(
            "Something the system received or observed from the outside world. "
            "The entry point for all external inputs."
        ),
    ),
    ObjectType(
        name="observation",
        schema=Observation,
        description=(
            "A source-grounded thing the system noticed. Weaker than a claim — "
            "records 'the system noticed this' without asserting truth."
        ),
    ),
    ObjectType(
        name="task",
        schema=Task,
        description=(
            "A minimal unit of work. Deliberately underpowered in Core — "
            "Team/Ops Pack adds project management features."
        ),
    ),
    ObjectType(
        name="action",
        schema=Action,
        description=(
            "A proposed or executed operation. Actions go through Tool Gateway "
            "before execution. The model proposes; the runtime authorizes."
        ),
    ),
    ObjectType(
        name="artifact",
        schema=Artifact,
        description=(
            "A durable output produced by the assistant: memo, draft, report, "
            "email draft, code patch, etc."
        ),
    ),
    ObjectType(
        name="memory_candidate",
        schema=MemoryCandidate,
        description=(
            "Something that might be worth remembering. Memory Gateway Pack "
            "handles acceptance, storage, and retrieval."
        ),
    ),
    ObjectType(
        name="evaluation",
        schema=Evaluation,
        description=(
            "A judgment about an object, action, task, artifact, or memory "
            "candidate. Used for quality control and memory acceptance."
        ),
    ),
]


# ================================================================ RelationType list

RELATION_TYPES = [
    RelationType(
        name="grounds",
        source_types=("source",),
        target_types=("observation",),
        description="A source grounds (provides evidence for) an observation.",
    ),
    RelationType(
        name="produces",
        source_types=("observation",),
        target_types=("task", "action", "artifact", "memory_candidate"),
        description="An observation produces a downstream object.",
    ),
    RelationType(
        name="executes",
        source_types=("action",),
        target_types=("task",),
        description="An action executes (fulfills) a task.",
    ),
    RelationType(
        name="generates",
        source_types=("task", "action"),
        target_types=("artifact",),
        description="A task or action generates an artifact.",
    ),
    RelationType(
        name="proposes",
        source_types=("observation", "action"),
        target_types=("memory_candidate",),
        description="An observation or action proposes a memory candidate.",
    ),
    RelationType(
        name="evaluates",
        source_types=("evaluation",),
        target_types=("memory_candidate", "artifact", "action", "task", "observation"),
        description="An evaluation judges a subject object.",
    ),
    RelationType(
        name="derived_from",
        source_types=(
            "source", "observation", "task", "action", "artifact",
            "memory_candidate", "evaluation",
        ),
        # Open on purpose (empty = any): bridge packs point derived_from at
        # domain objects (diligence claims, memos, risks, ...) that Core must
        # not enumerate — a closed list here would force Core to know domain
        # nouns, which violates "Core stays small".
        target_types=(),
        description=(
            "A downstream object is derived from a source object. "
            "Used by bridge packs to connect domain objects to Core objects."
        ),
    ),
    RelationType(
        name="derived_from_source",
        source_types=(
            # Core types
            "observation", "task", "action", "artifact", "memory_candidate", "evaluation",
            # Communication types
            "comm_message", "comm_thread", "comm_intent", "comm_response_candidate",
            # Domain types — Research, Meeting, Codebase, VC, Team/Ops
            "paper", "meeting", "transcript_segment",
            "repo", "issue", "pull_request", "architecture_decision", "code_change", "test_result",
            "company_profile", "founder_profile", "investment_memo", "deal_round",
            "email_message", "email_thread", "email_draft",
            "chat_input", "chat_session", "chat_turn",
        ),
        target_types=("source",),
        description=(
            "A domain or infrastructure object is derived from a Core source. "
            "Universal provenance relation — declared here so all packs share "
            "one definition and avoid PackConflictError."
        ),
    ),
]
