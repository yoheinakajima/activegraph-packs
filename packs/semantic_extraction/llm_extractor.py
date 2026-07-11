"""The LLM-backed annotation extractor (D025 stage two).

``semantic.llm@0.1.0`` registers beside ``semantic.deterministic@0.1.0``
at the declared registry seam: same annotation contract, same cache
identity scheme, different extractor id. Three properties this module
must hold:

1. **Recorded provider seam.** Every provider call goes through
   ``ReplayFirstProvider``: an existing record (the runtime's own
   prompt-hash-keyed fixture format) replays byte-exact and never
   re-contacts the provider; only an unseen prompt reaches the live
   provider, and its response is recorded before use. Re-extraction is
   therefore replay, and a graph rebuild reproduces byte-equal
   annotations with no key present.

2. **The LLM proposes, the extractor verifies.** The model returns
   candidate annotations with exact quoted spans; each span is verified
   byte-for-byte against the content and non-matching spans are dropped.
   An LLM may not mint an annotation whose anchor does not exist.

3. **No extra trust for fluency.** LLM-derived drafts carry this
   extractor's confidence (clamped), flow through the identical
   candidate projectors, and face the same promotion gates.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from .extractor import AnnotationDraft
from .facets import LLM_IMPLEMENTED_FACETS

_PROMPT_VERSION = "1"

_SYSTEM_PROMPT = """You are an annotation extractor for a provenance-first \
knowledge graph. Extract annotations from the user-supplied CONTENT for \
exactly the requested FACETS, and reply with a JSON array (no prose, no \
code fences). Each element:

{"facet": "<facet name>",
 "exact": "<verbatim substring of CONTENT — copied byte-for-byte>",
 "occurrence": <1-based index if the substring appears more than once>,
 "body": {<facet body, see below>},
 "modality": "stated" | "uncertain" | "hypothetical",
 "polarity": "positive" | "negative",
 "confidence": <0.0-1.0>,
 "event_time": "<ISO date if the annotation is about a dated event, else null>"}

Facet bodies:
- entity_mention: {"text": exact, "kind": "person" | "organization" | \
"place" | "product" | "handle" | "email" | "url" | "other", \
"normalized": "<canonical form>"}
- assertion: {"text": exact} — judge modality (stated/uncertain/\
hypothetical) and polarity (positive/negative) carefully.
- preference_expression: {"text": exact, "cue": "<the preference cue word>"}
- relation_mention: {"text": exact, "subject": "...", "predicate": "...", \
"object": "..."}
- event_mention: {"text": exact, "name": "<short event name>", \
"participants": ["..."], "when": "<ISO date or null>"}

Rules: "exact" MUST be a verbatim contiguous substring of CONTENT — never \
paraphrase, never fix typos, never merge spans. Only emit facets that were \
requested. Prefer precision over recall."""


def _clamp(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _strip_code_fence(raw: str) -> str:
    text = raw.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return match.group(1) if match else text


class ReplayFirstProvider:
    """Replay from the record directory; only unseen prompts go live.

    Composed entirely from runtime pieces: ``RecordedLLMProvider`` reads
    the record, ``RecordingLLMProvider`` wraps the live provider and
    persists the response before returning it. With ``live=None`` a
    missing record raises loudly — there is no silent fallthrough to a
    network call and no degraded output.
    """

    def __init__(self, record_dir: str, live: Optional[Any] = None) -> None:
        from activegraph.llm import RecordedLLMProvider, RecordingLLMProvider

        self._recorded = RecordedLLMProvider(record_dir)
        self._recording = (
            RecordingLLMProvider(live, record_dir) if live is not None else None
        )
        self.live_calls = 0

    def complete(self, **kwargs: Any):
        from activegraph.llm.errors import LLMBehaviorError

        try:
            return self._recorded.complete(**kwargs)
        except LLMBehaviorError:
            if self._recording is None:
                raise
            self.live_calls += 1
            return self._recording.complete(**kwargs)


class LLMExtractorV1:
    """LLM-backed extractor at the shared registry seam."""

    extractor_id = "semantic.llm"
    extractor_version = "0.1.0"

    def __init__(
        self,
        *,
        provider: Any,
        model: str,
        max_content_chars: int = 32_000,
        max_annotations_per_facet: int = 50,
        max_output_tokens: int = 4_096,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not model:
            raise ValueError(
                "semantic.llm needs an explicit model — it is part of the "
                "extractor's cache identity (config_hash) and must be "
                "stable across record and replay"
            )
        self._provider = provider
        self._model = model
        self._max_content_chars = max_content_chars
        self._max_per_facet = max_annotations_per_facet
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds

    def implemented_facets(self) -> tuple[str, ...]:
        return LLM_IMPLEMENTED_FACETS

    def config(self) -> dict[str, Any]:
        """Everything that shapes output — part of the cache identity."""
        return {
            "model": self._model,
            "prompt_version": _PROMPT_VERSION,
            "max_content_chars": self._max_content_chars,
            "max_annotations_per_facet": self._max_per_facet,
            "max_output_tokens": self._max_output_tokens,
        }

    # -- provider call -------------------------------------------------------

    def _complete(self, content: str, facets: tuple[str, ...]):
        from activegraph.llm import LLMMessage

        user = (
            f"FACETS: {json.dumps(sorted(facets))}\n"
            f"CONTENT:\n{content}"
        )
        return self._provider.complete(
            system=_SYSTEM_PROMPT,
            messages=[LLMMessage(role="user", content=user)],
            model=self._model,
            max_tokens=self._max_output_tokens,
            temperature=0.0,
            top_p=1.0,
            output_schema=None,
            timeout_seconds=self._timeout_seconds,
        )

    # -- extraction ------------------------------------------------------------

    def extract(
        self, content: str, metadata: dict[str, Any], facets: tuple[str, ...]
    ) -> list[AnnotationDraft]:
        del metadata  # attribution is mapped by the caller from evidence
        requested = tuple(sorted(set(facets) & set(LLM_IMPLEMENTED_FACETS)))
        if not requested:
            return []
        text = content[: self._max_content_chars]
        response = self._complete(text, requested)
        items = self._parse_items(response.raw_text)
        drafts = self._verify_and_draft(text, requested, items)
        drafts.sort(key=lambda d: (d.facet, d.start, d.end))
        capped: list[AnnotationDraft] = []
        per_facet: dict[str, int] = {}
        for draft in drafts:
            count = per_facet.get(draft.facet, 0)
            if count >= self._max_per_facet:
                continue
            per_facet[draft.facet] = count + 1
            capped.append(draft)
        return capped

    @staticmethod
    def _parse_items(raw_text: str) -> list[dict[str, Any]]:
        try:
            parsed = json.loads(_strip_code_fence(raw_text))
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, dict)]

    def _verify_and_draft(
        self,
        text: str,
        requested: tuple[str, ...],
        items: list[dict[str, Any]],
    ) -> list[AnnotationDraft]:
        drafts: list[AnnotationDraft] = []
        for item in items:
            facet = item.get("facet")
            exact = item.get("exact")
            if facet not in requested or not isinstance(exact, str) or not exact:
                continue

            # Byte-for-byte selector verification: the proposed span must
            # exist verbatim in the content or the annotation is dropped.
            occurrence = item.get("occurrence")
            start = _locate(text, exact, occurrence)
            if start is None:
                continue

            body = self._facet_body(facet, exact, item.get("body"))
            if body is None:
                continue

            modality = item.get("modality")
            if modality not in ("stated", "uncertain", "hypothetical"):
                modality = "stated"
            polarity = item.get("polarity")
            if polarity not in ("positive", "negative"):
                polarity = "positive"
            event_time = item.get("event_time")
            if not isinstance(event_time, str) or not event_time:
                event_time = None

            drafts.append(
                AnnotationDraft(
                    facet=facet,
                    body=body,
                    start=start,
                    end=start + len(exact),
                    exact=exact,
                    confidence=_clamp(item.get("confidence"), 0.6),
                    modality=modality,
                    polarity=polarity,
                    event_time=event_time,
                    metadata={"proposed_by": "llm"},
                )
            )
        return drafts

    @staticmethod
    def _facet_body(
        facet: str, exact: str, raw_body: Any
    ) -> Optional[dict[str, Any]]:
        """Shape and sanity-check one facet body; None drops the item.

        Full schema validation still happens in the engine
        (``validate_body``) — this only normalizes the LLM's output into
        the typed shape so malformed items are dropped rather than
        failing the whole run.
        """
        body = raw_body if isinstance(raw_body, dict) else {}
        if facet == "entity_mention":
            kind = body.get("kind")
            if kind not in (
                "handle", "email", "url", "proper_noun",
                "person", "organization", "place", "product", "other",
            ):
                kind = "other"
            normalized = body.get("normalized")
            if not isinstance(normalized, str) or not normalized:
                normalized = exact
            return {"text": exact, "kind": kind, "normalized": normalized}
        if facet == "assertion":
            return {"text": exact}
        if facet == "preference_expression":
            cue = body.get("cue")
            if not isinstance(cue, str) or not cue:
                return None
            return {"text": exact, "cue": cue.lower()}
        if facet == "relation_mention":
            subject = body.get("subject")
            predicate = body.get("predicate")
            obj = body.get("object")
            if not all(
                isinstance(part, str) and part
                for part in (subject, predicate, obj)
            ):
                return None
            return {
                "text": exact,
                "subject": subject,
                "predicate": predicate,
                "object": obj,
            }
        if facet == "event_mention":
            name = body.get("name")
            if not isinstance(name, str) or not name:
                return None
            participants = body.get("participants")
            if not isinstance(participants, list):
                participants = []
            participants = [
                part for part in participants if isinstance(part, str) and part
            ]
            when = body.get("when")
            if not isinstance(when, str) or not when:
                when = None
            return {
                "text": exact,
                "name": name,
                "participants": participants,
                "when": when,
            }
        return None


def _locate(text: str, exact: str, occurrence: Any) -> Optional[int]:
    """Find the byte-exact span; None when the anchor doesn't exist."""
    if isinstance(occurrence, int) and occurrence >= 1:
        start = -1
        for _ in range(occurrence):
            start = text.find(exact, start + 1)
            if start < 0:
                return None
        return start
    start = text.find(exact)
    return start if start >= 0 else None


def build_llm_extractor(settings: Any) -> LLMExtractorV1:
    """Construct the LLM extractor from settings + the configured provider.

    The provider stack: ``llm_record_dir`` set → replay-first over the
    record (live only for unseen prompts, and only when a provider is
    configured — a keyless replay works entirely from the record).
    Without a record dir a live provider is called directly; the engine's
    extraction_run cache still prevents any same-identity re-run.
    """
    from packs.llm_provider import configured_llm_provider, get_llm_provider

    live = get_llm_provider()
    resolved = configured_llm_provider()
    model = getattr(settings, "llm_model", "") or (
        getattr(live, "default_model", "") if live is not None else ""
    ) or (resolved.model or "")

    record_dir = getattr(settings, "llm_record_dir", None)
    if record_dir:
        provider: Any = ReplayFirstProvider(record_dir, live=live)
    elif live is not None:
        provider = live
    else:
        raise RuntimeError(
            "semantic.llm needs a configured LLM provider or a "
            "llm_record_dir to replay from (zero-key mode uses the "
            "deterministic extractor only)"
        )

    return LLMExtractorV1(
        provider=provider,
        model=model,
        max_content_chars=settings.max_content_chars,
        max_annotations_per_facet=settings.max_annotations_per_facet,
        max_output_tokens=getattr(settings, "llm_max_output_tokens", 4_096),
        timeout_seconds=getattr(settings, "llm_timeout_seconds", 60.0),
    )


__all__ = [
    "LLMExtractorV1",
    "ReplayFirstProvider",
    "build_llm_extractor",
]
