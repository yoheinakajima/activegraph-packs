"""Campaign bounds as configuration, never hardcoded doctrine (ADR 0045)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WebResearchSettings(BaseModel):
    """Versioned defaults for one owner-research campaign.

    Every bound here is authoritative over any model recommendation; the
    plan proposal copies the resolved values into its campaign disclosure so
    the owner approves exactly what will run.
    """

    model_config = ConfigDict(extra="forbid")

    max_rounds: int = Field(default=3, ge=1)
    max_total_queries: int = Field(default=12, ge=1)
    max_pages: int = Field(default=20, ge=1)
    max_follow_ups_per_round: int = Field(default=3, ge=0)
    max_findings_per_query: int = Field(default=5, ge=1)
    # Whether bounded, within-scope follow-up queries may execute without a
    # per-query owner verdict. Scope-expanding queries always pause as
    # amendments regardless of this flag.
    auto_follow_up: bool = True
    timeout_seconds_per_call: float = Field(default=90.0, gt=0)
    max_tokens_per_call: int = Field(default=1_500, ge=1)
    # Deterministic novelty floor: a round that yields fewer than this many
    # previously-unseen URLs stops the campaign (low marginal value).
    min_new_urls_per_round: int = Field(default=1, ge=0)
    # Sensitive-topic terms that pause any follow-up query for review.
    sensitive_topic_terms: list[str] = Field(default_factory=lambda: [
        "health", "medical", "diagnosis", "religion", "religious",
        "political", "politics", "criminal", "arrest", "lawsuit",
        "salary", "net worth", "ssn", "social security", "passport",
        "home address", "divorce", "sexuality",
    ])
    exclusions: list[str] = Field(default_factory=list)


__all__ = ["WebResearchSettings"]
