"""Settings for Memory Gateway Pack."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MemoryGatewaySettings(BaseModel):
    """Configuration for Memory Gateway Pack v0.1.

    Controls candidate evaluation, storage limits, and retrieval behavior.
    """

    acceptance_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum confidence score for a memory_candidate to be accepted "
            "and promoted to a MemoryItem. Candidates below this threshold "
            "are rejected with a 'low_confidence' judgment."
        ),
    )

    max_items: int = Field(
        default=1000,
        ge=1,
        description=(
            "Maximum number of MemoryItems to store. When exceeded, the "
            "least-recently-used items are evicted. Set to 0 for unlimited."
        ),
    )

    backend_url: str = Field(
        default=":memory:",
        description=(
            "Memory backend URL. Defaults to in-memory SQLite (no persistence "
            "across runs); a file path like 'memory.db' persists. A "
            "'scheme://' URL whose scheme was registered via "
            "backend.register_backend() selects an external store instead — "
            "e.g. 'mem0://default' after adapters.register_mem0_backend(). "
            "Must match ChatSettings.memory_backend_url so recall reads the "
            "store the writer writes."
        ),
    )

    retrieval_top_k: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of MemoryItems returned per retrieval.",
    )

    min_retrieval_score: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Minimum similarity score for a MemoryItem to appear in retrieval results.",
    )

    auto_accept_categories: list[str] = Field(
        default=["preference", "instruction", "decision"],
        description=(
            "Priority memory categories: candidates in these categories are "
            "accepted at auto_accept_min_confidence instead of the full "
            "acceptance_threshold — durable guidance is worth keeping even "
            "from a lower-confidence extraction. (Before v0.2 this setting "
            "was documented but had no effect.)"
        ),
    )

    auto_accept_min_confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "The reduced acceptance bar applied to auto_accept_categories. "
            "Kept above zero on purpose: category priority relieves the "
            "threshold, it does not suspend judgment."
        ),
    )

    provenance_admission: str = Field(
        default="trusted_senders",
        description=(
            "Admission policy for WHOSE words become memory: "
            "'trusted_senders' (default) — conversations build memory (the "
            "speaker is talking to the assistant and the memory is scoped to "
            "them), but guidance categories (instruction/preference/decision) "
            "extracted from NON-conversational content (emails, documents, "
            "tool results) are rejected unless the sender resolves to a "
            "trusted principal (owner/admin/collaborator). Documents don't "
            "give orders. 'off' — admit everything (pre-v0.2 behavior). "
            "Enforced only when the Identity/Auth Pack has registered "
            "principals — verification happens when verification is possible."
        ),
    )
