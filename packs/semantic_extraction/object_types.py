"""The shared annotation layer's graph schemas (ADR 0026).

One provenance envelope (``semantic_annotation``) shared by all typed
facet bodies; a cache-identified run record; first-class coverage; and
the ``extraction_profile`` versioned config artifact (D042) that decides
which facets run eagerly per source category.

Extraction here produces annotations, never domain candidates — the
candidate projectors in behaviors.py are separate, per-domain policy.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Optional

from activegraph.packs import ObjectType, RelationType
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .facets import STANDARD_FACETS

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

Attribution = Literal["subject_self", "author_about_subject", "unknown"]
Modality = Literal["stated", "uncertain", "hypothetical"]
Polarity = Literal["positive", "negative"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _is_standard_or_namespaced(facet: str) -> bool:
    return facet in STANDARD_FACETS or ("." in facet and not facet.startswith("."))


class SemanticAnnotation(_StrictModel):
    """One typed, source-anchored annotation under the shared envelope.

    Every field of the ADR 0026 envelope is here: evidence id + revision,
    exact selector, extractor id/version/config hash, confidence,
    author-vs-subject attribution, event time vs observation time,
    modality, polarity, and invalidation status.
    """

    annotation_identity: str = Field(min_length=1)
    facet: str = Field(min_length=1)
    body: dict[str, Any]

    # -- source anchor -----------------------------------------------------
    evidence_id: str = Field(min_length=1)
    evidence_identity: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    selector: dict[str, Any]

    # -- extractor identity --------------------------------------------------
    extractor_id: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    config_hash: str

    # -- epistemics ----------------------------------------------------------
    confidence: float = Field(ge=0.0, le=1.0)
    attribution: Attribution = "unknown"
    author_role: Optional[str] = None
    event_time: Optional[str] = None
    observation_time: Optional[str] = None
    modality: Modality = "stated"
    polarity: Polarity = "positive"

    # -- lifecycle -------------------------------------------------------------
    status: Literal["active", "invalidated"] = "active"
    invalidation_reason: Optional[str] = None
    run_id: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("facet")
    @classmethod
    def _known_facet(cls, value: str) -> str:
        if not _is_standard_or_namespaced(value):
            raise ValueError(
                f"facet {value!r} is neither a standard facet nor namespaced"
            )
        return value

    @field_validator("config_hash")
    @classmethod
    def _valid_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("must be a lowercase 64-character SHA-256 hex digest")
        return value

    @field_validator("selector")
    @classmethod
    def _exact_selector(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("kind") != "char_span":
            raise ValueError("selector.kind must be 'char_span' in v1")
        for key in ("start", "end"):
            if not isinstance(value.get(key), int) or value[key] < 0:
                raise ValueError(f"selector.{key} must be a non-negative int")
        if value["end"] < value["start"]:
            raise ValueError("selector.end must be >= selector.start")
        if not isinstance(value.get("exact"), str):
            raise ValueError("selector.exact must carry the exact quoted span")
        return value


class ExtractionRun(_StrictModel):
    """One cache-identified extractor pass over one evidence revision.

    The cache identity is exactly
    ``(evidence_revision, extractor_id, extractor_version, config_hash,
    requested_facets)`` (INGESTION_DOCTRINE). Re-extraction with the same
    identity is a no-op; a different requested set executes only the
    facets no prior run of the same extractor identity has produced.
    """

    run_identity: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    evidence_identity: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    selection_id: Optional[str] = None
    extractor_id: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    config_hash: str
    requested_facets: list[str]
    executed_facets: list[str] = Field(default_factory=list)
    cached_facets: list[str] = Field(default_factory=list)
    annotation_ids: list[str] = Field(default_factory=list)
    status: Literal["completed", "failed", "invalidated"] = "completed"
    error: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("config_hash")
    @classmethod
    def _valid_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("must be a lowercase 64-character SHA-256 hex digest")
        return value


class ExtractionCoverage(_StrictModel):
    """What one run did and did not process — a first-class output.

    Downstream count/sum/temporal/absence proofs check coverage instead
    of assuming completeness (GLOSSARY: extraction coverage).
    """

    coverage_identity: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    processed_facets: list[str] = Field(default_factory=list)
    skipped_facets: list[dict[str, str]] = Field(default_factory=list)
    content_chars_total: int = Field(ge=0)
    content_chars_processed: int = Field(ge=0)
    truncated: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class SelectionExtraction(_StrictModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    exact_hash: str = Field(min_length=64, max_length=64)


class SelectionExtractionRequest(_StrictModel):
    """A generic exact-span request from a domain projector.

    The requesting pack selects authoritative evidence spans; this pack owns
    extractor resolution, execution, caching, and settlement.
    """

    request_identity: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    selection_id: str = Field(min_length=1)
    selections: list[SelectionExtraction]
    requested_facets: list[str]
    status: Literal["proposed", "completed", "failed"] = "proposed"
    run_ids: list[str] = Field(default_factory=list)
    annotation_ids: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractionProfile(_StrictModel):
    """Versioned, owner-editable facet policy per source category (D042).

    Which facets run eagerly per source category is config, never
    architecture. Consumer requirements union. Superseded versions stay
    in the graph; exactly one profile is ``active`` at a time.
    """

    profile_identity: str = Field(min_length=1)
    version: int = Field(ge=1)
    status: Literal["active", "superseded"] = "active"
    default_facets: list[str]
    facets_by_source_category: dict[str, list[str]] = Field(default_factory=dict)
    extractor_by_facet: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "facet → 'extractor_id@version'. Facets absent from the map "
            "run on the settings default extractor; an empty map is the "
            "single-extractor behavior."
        ),
    )
    created_by: str = Field(min_length=1)
    rationale: str = ""
    supersedes_profile_id: Optional[str] = None


class AnnotationExtractorState(_StrictModel):
    """Eligibility of one annotation-extractor version.

    Disabling a version (via the invalidation tool) demotes its
    annotations and dependent candidates through provenance while the
    evidence stays intact (ADR 0014: candidates reversible without
    evidence deletion).
    """

    state_identity: str = Field(min_length=1)
    extractor_id: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    # "candidate" and "promoted" carry the fork-trial-promote lifecycle
    # (ADR 0014): a new extractor version lands as a candidate
    # configuration; recorded trial evidence plus an explicit approval
    # promote it. "disabled" demotes annotations via provenance.
    status: Literal["enabled", "disabled", "candidate", "promoted"] = "enabled"
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractorPromotionEvidence(_StrictModel):
    """One recorded extractor trial — the ADR 0014 promotion-evidence shape.

    A candidate extractor version is compared against the baseline on
    recorded content (both extractors, same facets, same evidence); the
    per-facet comparison is the evidence an explicit promotion cites.
    The trial itself changes no policy — promotion is a separate,
    approver-named step.
    """

    evidence_identity: str = Field(min_length=1)
    candidate_extractor_id: str = Field(min_length=1)
    candidate_extractor_version: str = Field(min_length=1)
    baseline_extractor_id: str = Field(min_length=1)
    baseline_extractor_version: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    facets: list[str] = Field(min_length=1)
    # Per-facet counts: {"facet": {"baseline": n, "candidate": n,
    # "candidate_only": n, "baseline_only": n, "selector_drops": n}}
    comparison: dict[str, dict[str, int]] = Field(default_factory=dict)
    verdict: Literal["candidate_richer", "baseline_richer", "neutral"]
    rationale: str = ""
    created_by: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


OBJECT_TYPES = [
    ObjectType(
        "semantic_annotation",
        SemanticAnnotation,
        "A typed, source-anchored annotation under the shared provenance envelope.",
    ),
    ObjectType(
        "extraction_run",
        ExtractionRun,
        "One cache-identified annotation-extraction pass over an evidence revision.",
    ),
    ObjectType(
        "extraction_coverage",
        ExtractionCoverage,
        "What an extraction run did and did not process.",
    ),
    ObjectType(
        "selection_extraction_request",
        SelectionExtractionRequest,
        "A generic exact-evidence-span extraction request from a domain projector.",
    ),
    ObjectType(
        "extraction_profile",
        ExtractionProfile,
        "Versioned owner-editable facet policy per source category.",
    ),
    ObjectType(
        "annotation_extractor_state",
        AnnotationExtractorState,
        "Eligibility state of an annotation-extractor version.",
    ),
    ObjectType(
        "extractor_promotion_evidence",
        ExtractorPromotionEvidence,
        "A recorded extractor trial: candidate vs baseline on recorded "
        "content (ADR 0014 promotion evidence).",
    ),
]

# Every candidate type a projector may mint from annotations — the
# semantic_extraction projectors (profile, memory) plus the
# activity_normalizer compatibility projectors (ADR 0026 step 2).
_PROJECTED_CANDIDATE_TYPES = (
    "profile_candidate",
    "memory_candidate",
    "preference_candidate",
    "task_candidate",
    "skill_candidate",
    "eval_candidate",
)

RELATION_TYPES = [
    RelationType(
        "annotation_for",
        source_types=("semantic_annotation",),
        target_types=("activity_evidence",),
        description="An annotation anchors to one evidence revision.",
    ),
    RelationType(
        "produced_annotation",
        source_types=("extraction_run",),
        target_types=("semantic_annotation",),
        description="A cache-identified run produced an annotation.",
    ),
    RelationType(
        "run_for",
        source_types=("extraction_run",),
        target_types=("activity_evidence",),
        description="An extraction run evaluated one evidence revision.",
    ),
    RelationType(
        "coverage_for",
        source_types=("extraction_coverage",),
        target_types=("extraction_run",),
        description="A coverage record describes one extraction run.",
    ),
    RelationType(
        "projected_from_annotation",
        source_types=_PROJECTED_CANDIDATE_TYPES,
        target_types=("semantic_annotation",),
        description="A domain candidate was minted by a projector from an annotation.",
    ),
]


__all__ = [
    "SemanticAnnotation",
    "ExtractionRun",
    "ExtractionCoverage",
    "SelectionExtractionRequest",
    "ExtractionProfile",
    "AnnotationExtractorState",
    "ExtractorPromotionEvidence",
    "Attribution",
    "Modality",
    "Polarity",
    "OBJECT_TYPES",
    "RELATION_TYPES",
]
