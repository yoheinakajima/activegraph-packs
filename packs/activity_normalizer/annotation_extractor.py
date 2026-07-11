"""The legacy structure extractor as an annotation emitter (ADR 0026 step 2).

``activity.structure@0.2.0`` registers at the shared extraction seam and
emits the exact same findings the legacy 0.1.0 candidate extractor did —
now as source-anchored annotations under namespaced extension facets
(``activity.memory``, ``activity.preference``, ``activity.task``,
``activity.profile``, ``activity.skill``, ``activity.eval``). The
compatibility candidate projectors in behaviors.py consume these and
mint the same candidate objects the direct write path used to, with the
same identity scheme, so re-running ingestion over already-extracted
evidence across the migration boundary creates nothing new.

Draft parity is load-bearing: (kind, text, ordinal) must match the
legacy extractor byte-for-byte, because the legacy candidate identity is
derived from them. The line-scanning logic below is the 0.1.0 logic with
span tracking added — do not "improve" the regex behavior here.
"""

from __future__ import annotations

import re
from typing import Any

from packs.semantic_extraction.extractor import (
    AnnotationDraft,
    register_annotation_extractor,
)

from .extractors import StructureExtractor

#: facet ↔ legacy candidate kind (namespaced extension facets, allowed by
#: the ADR 0026 contract; the standard ten are untouched).
FACET_BY_KIND: dict[str, str] = {
    "memory": "activity.memory",
    "preference": "activity.preference",
    "task": "activity.task",
    "profile": "activity.profile",
    "skill": "activity.skill",
    "eval": "activity.eval",
}
KIND_BY_FACET: dict[str, str] = {facet: kind for kind, facet in FACET_BY_KIND.items()}
STRUCTURE_FACETS: tuple[str, ...] = tuple(sorted(KIND_BY_FACET))

_CLEAN_RE = re.compile(r"\s+")


def _clean(value: str, limit: int) -> str:
    return _CLEAN_RE.sub(" ", value).strip()[:limit]


class StructureAnnotationExtractor:
    """The 0.1.0 structure heuristics, emitted as annotations."""

    extractor_id = "activity.structure"
    extractor_version = "0.2.0"

    def __init__(
        self,
        *,
        max_candidates: int = 32,
        max_candidate_chars: int = 2000,
    ) -> None:
        self._max_candidates = max_candidates
        self._max_candidate_chars = max_candidate_chars
        self._legacy = StructureExtractor()

    def implemented_facets(self) -> tuple[str, ...]:
        return STRUCTURE_FACETS

    def config(self) -> dict[str, Any]:
        return {
            "max_candidates": self._max_candidates,
            "max_candidate_chars": self._max_candidate_chars,
        }

    def extract(
        self, content: str, metadata: dict[str, Any], facets: tuple[str, ...]
    ) -> list[AnnotationDraft]:
        requested_kinds = {
            KIND_BY_FACET[facet] for facet in facets if facet in KIND_BY_FACET
        }
        if not requested_kinds:
            return []

        # The exact legacy draft list decides (kind, text, ordinal) —
        # ordinals are positions in THIS list, before facet filtering,
        # so they match the legacy candidate identities regardless of
        # which facets a profile requests.
        drafts = self._legacy.extract(
            content,
            metadata,
            max_candidates=self._max_candidates,
            max_candidate_chars=self._max_candidate_chars,
        )

        spans = _line_spans(content, self._max_candidate_chars)
        out: list[AnnotationDraft] = []
        for ordinal, draft in enumerate(drafts):
            if draft.kind not in requested_kinds:
                continue
            start, end, exact = _anchor(draft.text, draft.kind, spans, content)
            body: dict[str, Any] = {
                "text": draft.text,
                "kind": draft.kind,
                **draft.fields,
            }
            out.append(
                AnnotationDraft(
                    facet=FACET_BY_KIND[draft.kind],
                    body=body,
                    start=start,
                    end=end,
                    exact=exact,
                    confidence=draft.confidence,
                    metadata={"ordinal": ordinal},
                )
            )
        return out


def _line_spans(
    content: str, max_candidate_chars: int
) -> list[tuple[int, int, str, str]]:
    """(start, end, raw, cleaned) per non-empty line, offsets exact."""
    spans: list[tuple[int, int, str, str]] = []
    cursor = 0
    for raw in content.splitlines():
        start = content.find(raw, cursor)
        if start < 0:  # pragma: no cover - splitlines yields substrings
            continue
        cursor = start + len(raw)
        cleaned = _clean(raw, max_candidate_chars)
        if cleaned:
            spans.append((start, start + len(raw), raw, cleaned))
    if not spans and content.strip():
        stripped = content.strip()
        start = content.find(stripped)
        spans.append(
            (
                start,
                start + len(stripped),
                stripped,
                _clean(content, max_candidate_chars),
            )
        )
    return spans


def _anchor(
    text: str, kind: str, spans: list[tuple[int, int, str, str]], content: str
) -> tuple[int, int, str]:
    """Anchor one legacy draft to the line it came from.

    Prefer the byte-exact occurrence of the draft text; prefix-stripped
    or whitespace-collapsed drafts anchor to their originating line's
    raw span instead — the selector always quotes real content bytes.
    """
    direct = content.find(text)
    if direct >= 0:
        return direct, direct + len(text), text
    needle = text.casefold()
    for start, end, raw, cleaned in spans:
        if needle in cleaned.casefold() or cleaned.casefold() in needle:
            return start, end, raw
    # Last resort: the first line (the memory draft's home).
    start, end, raw, _cleaned = spans[0]
    return start, end, raw


register_annotation_extractor(StructureAnnotationExtractor())


__all__ = [
    "FACET_BY_KIND",
    "KIND_BY_FACET",
    "STRUCTURE_FACETS",
    "StructureAnnotationExtractor",
]
