"""Versioned deterministic extractor seam for activity evidence.

Only the zero-key structure extractor ships in v0.1.  The registry accepts
additional implementations later, including an LLM-backed extractor supplied
by a host, without changing evidence identity or the graph contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class CandidateDraft:
    """Provider-neutral proposed candidate before graph materialization."""

    kind: str
    text: str
    confidence: float = 0.7
    fields: dict[str, Any] = field(default_factory=dict)


class Extractor(Protocol):
    """Interface future deterministic or configured provider extractors use."""

    extractor_id: str
    extractor_version: str

    def extract(
        self,
        content: str,
        metadata: dict[str, Any],
        *,
        max_candidates: int,
        max_candidate_chars: int,
    ) -> list[CandidateDraft]: ...


def _clean(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit]


class StructureExtractor:
    """Small, deterministic line/structure heuristics with no provider key."""

    extractor_id = "activity.structure"
    extractor_version = "0.1.0"

    _PREFERENCE = re.compile(r"^(?:preference\s*:|i\s+(?:always\s+)?prefer\b|i\s+like\b)", re.I)
    _TASK = re.compile(r"^(?:todo\s*:|task\s*:|action\s*:|we\s+need\s+to\b)", re.I)
    _PROFILE = re.compile(r"^(?:profile\s*:|my\s+name\s+is\b|i\s+am\b)", re.I)
    _SKILL = re.compile(r"^skill\s*:", re.I)
    _EVAL = re.compile(
        r"^(?:evaluation\s*:|eval\s*:)|\b(?:helpful|helped|hurt|failed|worked|contradicted)\b",
        re.I,
    )
    _MEMORY_PREFIX = re.compile(r"^(?:remember\s*:|memory\s*:)", re.I)

    def extract(
        self,
        content: str,
        metadata: dict[str, Any],
        *,
        max_candidates: int,
        max_candidate_chars: int,
    ) -> list[CandidateDraft]:
        lines = [
            _clean(line, max_candidate_chars)
            for line in content.splitlines()
            if _clean(line, max_candidate_chars)
        ]
        if not lines and content.strip():
            lines = [_clean(content, max_candidate_chars)]

        drafts: list[CandidateDraft] = []
        if lines:
            first = self._MEMORY_PREFIX.sub("", lines[0]).strip() or lines[0]
            drafts.append(
                CandidateDraft(
                    "memory",
                    first,
                    0.72,
                    {"category": "context"},
                )
            )

        for line in lines:
            if self._PREFERENCE.search(line):
                text = re.sub(r"^preference\s*:\s*", "", line, flags=re.I)
                if text:
                    drafts.append(CandidateDraft("preference", text, 0.82, {"preference": text}))
            if self._TASK.search(line):
                text = re.sub(r"^(?:todo|task|action)\s*:\s*", "", line, flags=re.I)
                if text:
                    drafts.append(CandidateDraft("task", text, 0.8, {"title": text[:120]}))
            if self._PROFILE.search(line):
                text = re.sub(r"^profile\s*:\s*", "", line, flags=re.I)
                if text:
                    drafts.append(
                        CandidateDraft(
                            "profile",
                            text,
                            0.78,
                            {"attribute": "profile_statement", "value": text},
                        )
                    )
            if self._SKILL.search(line):
                text = re.sub(r"^skill\s*:\s*", "", line, flags=re.I)
                if text:
                    drafts.append(
                        CandidateDraft(
                            "skill",
                            text,
                            0.76,
                            {"name": text[:120], "description": text},
                        )
                    )
            if self._EVAL.search(line):
                text = re.sub(r"^(?:evaluation|eval)\s*:\s*", "", line, flags=re.I)
                if text:
                    drafts.append(
                        CandidateDraft(
                            "eval",
                            text,
                            0.74,
                            {"subject": "source_activity", "judgment": text},
                        )
                    )

        # One stable output for identical text/kind, preserving first occurrence.
        unique: list[CandidateDraft] = []
        seen: set[tuple[str, str]] = set()
        for draft in drafts:
            key = (draft.kind, draft.text.casefold())
            if key in seen:
                continue
            seen.add(key)
            unique.append(draft)
            if len(unique) >= max_candidates:
                break
        return unique


_REGISTRY: dict[tuple[str, str], Extractor] = {}


def register_extractor(extractor: Extractor) -> None:
    """Register an implementation by immutable id/version."""

    key = (extractor.extractor_id, extractor.extractor_version)
    existing = _REGISTRY.get(key)
    if existing is not None and existing is not extractor:
        raise ValueError(f"extractor already registered: {key[0]}@{key[1]}")
    _REGISTRY[key] = extractor


def get_extractor(extractor_id: str, extractor_version: str) -> Extractor:
    try:
        return _REGISTRY[(extractor_id, extractor_version)]
    except KeyError as exc:
        raise KeyError(f"unknown extractor {extractor_id}@{extractor_version}") from exc


register_extractor(StructureExtractor())


__all__ = [
    "CandidateDraft",
    "Extractor",
    "StructureExtractor",
    "register_extractor",
    "get_extractor",
]
