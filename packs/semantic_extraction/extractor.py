"""Deterministic annotation extractor v1 — the zero-key floor.

Pure string/regex operations, byte-deterministic, no wall-clock, no
network, no keys. LLM-upgraded extraction is a different extractor id
behind the same registry seam (``register_annotation_extractor``): same
contract, same envelope, different ``extractor_id`` — nothing else in
the pack changes.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from .facets import V1_IMPLEMENTED_FACETS


@dataclass(frozen=True)
class AnnotationDraft:
    """One proposed annotation before graph materialization."""

    facet: str
    body: dict[str, Any]
    start: int
    end: int
    exact: str
    confidence: float
    modality: str = "stated"
    polarity: str = "positive"
    event_time: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AnnotationExtractor(Protocol):
    extractor_id: str
    extractor_version: str

    def implemented_facets(self) -> tuple[str, ...]: ...

    def config(self) -> dict[str, Any]: ...

    def extract(
        self, content: str, metadata: dict[str, Any], facets: tuple[str, ...]
    ) -> list[AnnotationDraft]: ...


def config_hash_for(config: dict[str, Any]) -> str:
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- patterns

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_URL = re.compile(r"https?://[^\s)\]}>\"',]+")
_HANDLE = re.compile(r"(?<![\w@.])@[A-Za-z0-9_]{2,32}\b")
_PROPER_MULTI = re.compile(
    r"\b[A-Z][A-Za-z0-9&'’-]+(?:\s+[A-Z][A-Za-z0-9&'’-]+)+\b"
)
_PROPER_SINGLE = re.compile(r"\b[A-Z][a-z0-9'’-]{2,}\b")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_PREFERENCE_CUE = re.compile(
    r"\b(prefer(?:s|red)?|like(?:s)?|love(?:s)?|enjoy(?:s)?|hate(?:s)?|"
    r"dislike(?:s)?|favor(?:s|ite)?|favourite|always\s+use(?:s)?|"
    r"never\s+use(?:s)?|usually|rather\s+than|working\s+style)\b",
    re.IGNORECASE,
)
_NEGATIVE_CUE = re.compile(
    r"\b(hate(?:s)?|dislike(?:s)?|never|not|doesn'?t|don'?t|isn'?t|"
    r"aren'?t|won'?t|avoid(?:s)?)\b",
    re.IGNORECASE,
)
_UNCERTAIN_CUE = re.compile(
    r"\b(maybe|might|possibly|perhaps|probably|seems?|appear(?:s)?)\b",
    re.IGNORECASE,
)
_INTERROGATIVE_START = re.compile(
    r"^(who|what|when|where|why|how|is|are|do|does|did|can|could|would|"
    r"should|will)\b",
    re.IGNORECASE,
)

_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_MONTH_INDEX = {name: index + 1 for index, name in enumerate(_MONTHS)}
_MONTH_ALT = "|".join(_MONTHS)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MDY = re.compile(
    rf"\b({_MONTH_ALT})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b"
)
_DMY = re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_ALT})\s+(\d{{4}})\b")
_MY = re.compile(rf"\b({_MONTH_ALT})\s+(\d{{4}})\b")

_STOPWORDS = frozenset(
    """a about above after again all also am an and any are as at be because
    been before being below between both but by could did do does doing down
    during each few for from further had has have having he her here hers
    him his how i if in into is it its itself just like me more most my
    myself no nor not now of off on once only or other our ours out over own
    same she should so some such than that the their theirs them then there
    these they this those through to too under until up very was we were
    what when where which while who whom why will with you your yours
    everything anything something things using used use know knows tell
    told""".split()
)

_PROPER_STOP = frozenset(
    {"The", "This", "That", "There", "These", "Those", "When", "Where",
     "What", "Who", "Why", "How", "And", "But", "Also", "Then", "Here",
     "Yes", "No", "Not", "All", "Any", "Some", "Please", "Thanks"}
)


def _sentences(content: str) -> list[tuple[int, int, str]]:
    """Deterministic sentence spans: (start, end, text), offsets exact."""
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for match in _SENTENCE_SPLIT.finditer(content):
        chunk = content[cursor : match.start()]
        if chunk.strip():
            spans.append((cursor, match.start(), chunk))
        cursor = match.end()
    tail = content[cursor:]
    if tail.strip():
        spans.append((cursor, len(content), tail))
    trimmed: list[tuple[int, int, str]] = []
    for start, end, text in spans:
        left = len(text) - len(text.lstrip())
        right = len(text) - len(text.rstrip())
        trimmed.append((start + left, end - right, text.strip()))
    return trimmed


def _strip_bullet(text: str) -> str:
    return re.sub(r"^[-*•>\d.)\s]+", "", text)


class DeterministicExtractorV1:
    """Zero-key floor over recorded replay/normalized content."""

    extractor_id = "semantic.deterministic"
    extractor_version = "0.1.0"

    def __init__(
        self,
        *,
        max_content_chars: int = 32_000,
        max_annotations_per_facet: int = 50,
        min_assertion_chars: int = 15,
        topic_tag_count: int = 5,
    ) -> None:
        self._max_content_chars = max_content_chars
        self._max_per_facet = max_annotations_per_facet
        self._min_assertion_chars = min_assertion_chars
        self._topic_tag_count = topic_tag_count

    def implemented_facets(self) -> tuple[str, ...]:
        return V1_IMPLEMENTED_FACETS

    def config(self) -> dict[str, Any]:
        return {
            "max_content_chars": self._max_content_chars,
            "max_annotations_per_facet": self._max_per_facet,
            "min_assertion_chars": self._min_assertion_chars,
            "topic_tag_count": self._topic_tag_count,
        }

    # -- facet extractors ---------------------------------------------------

    def extract(
        self, content: str, metadata: dict[str, Any], facets: tuple[str, ...]
    ) -> list[AnnotationDraft]:
        del metadata  # attribution is mapped by the caller from evidence
        text = content[: self._max_content_chars]
        drafts: list[AnnotationDraft] = []
        for facet in facets:
            handler = getattr(self, f"_extract_{facet}", None)
            if handler is None:
                continue
            found = handler(text)
            drafts.extend(found[: self._max_per_facet])
        return drafts

    def _extract_entity_mention(self, text: str) -> list[AnnotationDraft]:
        drafts: list[AnnotationDraft] = []
        claimed: set[tuple[int, int]] = set()

        def _add(match_start: int, match_end: int, kind: str, exact: str,
                 normalized: str, confidence: float) -> None:
            span = (match_start, match_end)
            for start, end in claimed:
                if match_start < end and start < match_end:
                    return
            claimed.add(span)
            drafts.append(
                AnnotationDraft(
                    facet="entity_mention",
                    body={"text": exact, "kind": kind, "normalized": normalized},
                    start=match_start,
                    end=match_end,
                    exact=exact,
                    confidence=confidence,
                )
            )

        for match in _EMAIL.finditer(text):
            _add(match.start(), match.end(), "email", match.group(0),
                 match.group(0).lower(), 0.95)
        for match in _URL.finditer(text):
            exact = match.group(0).rstrip(".,;:!?")
            _add(match.start(), match.start() + len(exact), "url", exact,
                 exact.rstrip("/").lower(), 0.95)
        for match in _HANDLE.finditer(text):
            _add(match.start(), match.end(), "handle", match.group(0),
                 match.group(0).lower(), 0.9)
        for match in _PROPER_MULTI.finditer(text):
            exact = match.group(0)
            head = exact.split()[0]
            if head in _PROPER_STOP:
                continue
            _add(match.start(), match.end(), "proper_noun", exact, exact, 0.6)
        sentence_starts = {start for start, _end, _text in _sentences(text)}
        for match in _PROPER_SINGLE.finditer(text):
            if match.start() in sentence_starts:
                continue
            exact = match.group(0)
            if exact in _PROPER_STOP or exact.lower() in _STOPWORDS:
                continue
            if exact in _MONTH_INDEX:
                continue
            _add(match.start(), match.end(), "proper_noun", exact, exact, 0.55)
        drafts.sort(key=lambda draft: (draft.start, draft.end))
        return drafts

    def _extract_assertion(self, text: str) -> list[AnnotationDraft]:
        drafts: list[AnnotationDraft] = []
        for start, end, sentence in _sentences(text):
            stripped = _strip_bullet(sentence)
            if len(stripped) < self._min_assertion_chars:
                continue
            if stripped.endswith("?") or stripped.endswith(":"):
                continue
            if _INTERROGATIVE_START.match(stripped):
                continue
            if " " not in stripped:
                continue
            modality = "uncertain" if _UNCERTAIN_CUE.search(stripped) else "stated"
            polarity = "negative" if _NEGATIVE_CUE.search(stripped) else "positive"
            offset = start + (len(sentence) - len(sentence.lstrip()))
            offset += len(sentence.strip()) - len(stripped)
            drafts.append(
                AnnotationDraft(
                    facet="assertion",
                    body={"text": stripped},
                    start=offset,
                    end=offset + len(stripped),
                    exact=stripped,
                    confidence=0.7,
                    modality=modality,
                    polarity=polarity,
                )
            )
        return drafts

    def _extract_question(self, text: str) -> list[AnnotationDraft]:
        drafts: list[AnnotationDraft] = []
        for start, end, sentence in _sentences(text):
            stripped = _strip_bullet(sentence)
            if not stripped.endswith("?") or len(stripped) < 4:
                continue
            offset = end - len(stripped)
            drafts.append(
                AnnotationDraft(
                    facet="question",
                    body={"text": stripped},
                    start=offset,
                    end=end,
                    exact=stripped,
                    confidence=0.8,
                )
            )
        return drafts

    def _extract_preference_expression(self, text: str) -> list[AnnotationDraft]:
        drafts: list[AnnotationDraft] = []
        for start, end, sentence in _sentences(text):
            stripped = _strip_bullet(sentence)
            match = _PREFERENCE_CUE.search(stripped)
            if match is None or stripped.endswith("?"):
                continue
            polarity = "negative" if _NEGATIVE_CUE.search(stripped) else "positive"
            offset = end - len(stripped)
            drafts.append(
                AnnotationDraft(
                    facet="preference_expression",
                    body={"text": stripped, "cue": match.group(0).lower()},
                    start=offset,
                    end=end,
                    exact=stripped,
                    confidence=0.75,
                    polarity=polarity,
                )
            )
        return drafts

    def _extract_temporal_expression(self, text: str) -> list[AnnotationDraft]:
        drafts: list[AnnotationDraft] = []
        claimed: set[tuple[int, int]] = set()

        def _add(match_start: int, match_end: int, exact: str,
                 normalized: str, precision: str) -> None:
            for start, end in claimed:
                if match_start < end and start < match_end:
                    return
            claimed.add((match_start, match_end))
            drafts.append(
                AnnotationDraft(
                    facet="temporal_expression",
                    body={"text": exact, "normalized": normalized,
                          "precision": precision},
                    start=match_start,
                    end=match_end,
                    exact=exact,
                    confidence=0.9,
                    event_time=normalized,
                )
            )

        for match in _ISO_DATE.finditer(text):
            year, month, day = (int(part) for part in match.groups())
            if not (1 <= month <= 12 and 1 <= day <= 31):
                continue
            _add(match.start(), match.end(), match.group(0),
                 f"{year:04d}-{month:02d}-{day:02d}", "day")
        for match in _MDY.finditer(text):
            month = _MONTH_INDEX[match.group(1)]
            day, year = int(match.group(2)), int(match.group(3))
            if not 1 <= day <= 31:
                continue
            _add(match.start(), match.end(), match.group(0),
                 f"{year:04d}-{month:02d}-{day:02d}", "day")
        for match in _DMY.finditer(text):
            day, month, year = (
                int(match.group(1)),
                _MONTH_INDEX[match.group(2)],
                int(match.group(3)),
            )
            if not 1 <= day <= 31:
                continue
            _add(match.start(), match.end(), match.group(0),
                 f"{year:04d}-{month:02d}-{day:02d}", "day")
        for match in _MY.finditer(text):
            month, year = _MONTH_INDEX[match.group(1)], int(match.group(2))
            _add(match.start(), match.end(), match.group(0),
                 f"{year:04d}-{month:02d}", "month")
        drafts.sort(key=lambda draft: (draft.start, draft.end))
        return drafts

    def _extract_topic_tag(self, text: str) -> list[AnnotationDraft]:
        counts: dict[str, int] = {}
        first_span: dict[str, tuple[int, int]] = {}
        for match in re.finditer(r"[A-Za-z][A-Za-z0-9_-]{3,}", text):
            token = match.group(0).lower()
            if token in _STOPWORDS:
                continue
            counts[token] = counts.get(token, 0) + 1
            if token not in first_span:
                first_span[token] = (match.start(), match.end())
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        drafts: list[AnnotationDraft] = []
        for token, count in ranked[: self._topic_tag_count]:
            if count < 2:
                continue
            start, end = first_span[token]
            drafts.append(
                AnnotationDraft(
                    facet="topic_tag",
                    body={"tag": token, "occurrences": count},
                    start=start,
                    end=end,
                    exact=text[start:end],
                    confidence=0.5,
                )
            )
        return drafts


# ---------------------------------------------------------------- registry

_REGISTRY: dict[tuple[str, str], AnnotationExtractor] = {}


def register_annotation_extractor(extractor: AnnotationExtractor) -> None:
    """Register by immutable (extractor_id, extractor_version).

    This is the upgrade seam: an LLM-backed extractor registers here with
    its own id, and the extraction_profile / settings select it — the
    envelope, cache identity, and coverage contract are unchanged.
    """
    key = (extractor.extractor_id, extractor.extractor_version)
    existing = _REGISTRY.get(key)
    if existing is not None and existing is not extractor:
        raise ValueError(f"extractor already registered: {key}")
    _REGISTRY[key] = extractor


def get_annotation_extractor(
    extractor_id: str, extractor_version: str
) -> AnnotationExtractor:
    key = (extractor_id, extractor_version)
    if key not in _REGISTRY:
        raise KeyError(f"unknown annotation extractor: {key}")
    return _REGISTRY[key]


register_annotation_extractor(DeterministicExtractorV1())


__all__ = [
    "AnnotationDraft",
    "AnnotationExtractor",
    "DeterministicExtractorV1",
    "config_hash_for",
    "get_annotation_extractor",
    "register_annotation_extractor",
]
