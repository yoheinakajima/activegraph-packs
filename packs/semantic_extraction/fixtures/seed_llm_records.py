"""Seed the committed LLM extraction records for the pack fixtures.

Writes ``llm_records/*.json`` in the runtime's prompt-hash-keyed fixture
format by driving ``LLMExtractorV1`` through ``RecordingLLMProvider``
around a scripted provider. The records are what the keyless fixture and
CI replay byte-exact; re-running this script with a *live* provider (set
``ACTIVEGRAPH_SEED_LIVE=1`` with a key present) replaces them with real
model output — the replay contract is identical either way.

The scripted responses deliberately include one annotation whose exact
span does NOT occur in the content, so the fixtures also demonstrate the
selector-verification drop (an LLM may not mint an annotation whose
anchor doesn't exist).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from decimal import Decimal
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parents[2]))

from activegraph.llm import LLMResponse, RecordingLLMProvider

from packs.semantic_extraction.llm_extractor import LLMExtractorV1

RECORD_DIR = _HERE / "llm_records"
FIXTURE_MODEL = "fixture-llm-1"

SUMMARY = (
    "Yohei Nakajima is a general partner at Untapped Capital. "
    "He created BabyAGI on 2023-03-28 and shares projects at "
    "https://yoheinakajima.com. You can reach him at yohei@untapped.vc "
    "or @yoheinakajima. He prefers building small deterministic tools. "
    "What should the agent build next? "
    "He started the activegraph project in June 2026."
)

# The scripted "model output" per requested facet set. Spans are verbatim
# substrings of SUMMARY — except the one marked bogus, which verification
# must drop.
_ANNOTATIONS = [
    {
        "facet": "entity_mention",
        "exact": "Yohei Nakajima",
        "body": {"text": "Yohei Nakajima", "kind": "person",
                 "normalized": "yohei nakajima"},
        "confidence": 0.92,
    },
    {
        "facet": "entity_mention",
        "exact": "Untapped Capital",
        "body": {"text": "Untapped Capital", "kind": "organization",
                 "normalized": "untapped capital"},
        "confidence": 0.9,
    },
    {
        "facet": "entity_mention",
        "exact": "BabyAGI",
        "body": {"text": "BabyAGI", "kind": "product",
                 "normalized": "babyagi"},
        "confidence": 0.88,
    },
    {
        "facet": "entity_mention",
        "exact": "activegraph",
        "body": {"text": "activegraph", "kind": "product",
                 "normalized": "activegraph"},
        "confidence": 0.8,
    },
    {
        # Bogus on purpose: this span does not occur in the content.
        "facet": "entity_mention",
        "exact": "General Partner Yohei",
        "body": {"text": "General Partner Yohei", "kind": "person",
                 "normalized": "yohei nakajima"},
        "confidence": 0.9,
    },
    {
        "facet": "assertion",
        "exact": "Yohei Nakajima is a general partner at Untapped Capital.",
        "body": {"text": "Yohei Nakajima is a general partner at "
                         "Untapped Capital."},
        "modality": "stated",
        "polarity": "positive",
        "confidence": 0.9,
    },
    {
        "facet": "assertion",
        "exact": "He started the activegraph project in June 2026.",
        "body": {"text": "He started the activegraph project in June 2026."},
        "modality": "stated",
        "polarity": "positive",
        "confidence": 0.85,
        "event_time": "2026-06",
    },
    {
        "facet": "preference_expression",
        "exact": "He prefers building small deterministic tools.",
        "body": {"text": "He prefers building small deterministic tools.",
                 "cue": "prefers"},
        "polarity": "positive",
        "confidence": 0.85,
    },
    {
        "facet": "relation_mention",
        "exact": "Yohei Nakajima is a general partner at Untapped Capital",
        "body": {"text": "Yohei Nakajima is a general partner at "
                         "Untapped Capital",
                 "subject": "Yohei Nakajima",
                 "predicate": "general_partner_at",
                 "object": "Untapped Capital"},
        "confidence": 0.85,
    },
    {
        "facet": "relation_mention",
        "exact": "He created BabyAGI",
        "body": {"text": "He created BabyAGI",
                 "subject": "Yohei Nakajima",
                 "predicate": "created",
                 "object": "BabyAGI"},
        "confidence": 0.8,
    },
    {
        "facet": "event_mention",
        "exact": "created BabyAGI on 2023-03-28",
        "body": {"text": "created BabyAGI on 2023-03-28",
                 "name": "BabyAGI creation",
                 "participants": ["Yohei Nakajima"],
                 "when": "2023-03-28"},
        "event_time": "2023-03-28",
        "confidence": 0.85,
    },
    {
        "facet": "event_mention",
        "exact": "started the activegraph project in June 2026",
        "body": {"text": "started the activegraph project in June 2026",
                 "name": "activegraph project start",
                 "participants": ["Yohei Nakajima"],
                 "when": "2026-06"},
        "event_time": "2026-06",
        "confidence": 0.8,
    },
]


class ScriptedProvider:
    """Answers the extraction prompt with the scripted annotations for
    exactly the requested facets. Stands in for a live model when no
    key/network is available; recorded through the same seam."""

    default_model = FIXTURE_MODEL

    def complete(self, *, system, messages, model, max_tokens, temperature,
                 top_p, output_schema, timeout_seconds, tools=None,
                 structured_output_mode="prompt"):
        match = re.search(r"FACETS: (\[.*?\])", messages[0].content)
        facets = set(json.loads(match.group(1))) if match else set()
        items = [item for item in _ANNOTATIONS if item["facet"] in facets]
        raw = json.dumps(items, indent=1)
        return LLMResponse(
            raw_text=raw,
            parsed=None,
            input_tokens=len(system) // 4,
            output_tokens=len(raw) // 4,
            cost_usd=Decimal("0"),
            latency_seconds=0.0,
            model=model,
            finish_reason="end_turn",
            seed=None,
            cache_hit=False,
            provider_meta={"scripted": True},
            tool_calls=None,
        )

    def recognizes_model(self, name):
        return True

    def supports_native_structured_output(self, model):
        return False

    def estimate_cost(self, *, input_tokens, output_tokens, model):
        return Decimal("0")

    def count_tokens(self, *, system, messages, model):
        return max(1, (len(system) + sum(len(m.content) for m in messages)) // 4)


def _live_inner():
    from packs.llm_provider import get_llm_provider

    provider = get_llm_provider()
    if provider is None:
        raise SystemExit(
            "ACTIVEGRAPH_SEED_LIVE=1 needs ANTHROPIC_API_KEY or "
            "OPENAI_API_KEY in the environment"
        )
    return provider


def main() -> int:
    live = os.environ.get("ACTIVEGRAPH_SEED_LIVE") == "1"
    inner = _live_inner() if live else ScriptedProvider()
    model = getattr(inner, "default_model", FIXTURE_MODEL) if live else FIXTURE_MODEL
    RECORD_DIR.mkdir(exist_ok=True)
    for stale in RECORD_DIR.glob("*.json"):
        stale.unlink()
    provider = RecordingLLMProvider(inner, str(RECORD_DIR))
    extractor = LLMExtractorV1(provider=provider, model=model)

    # The three prompts the fixtures replay: the default-profile upgrade
    # group, the full trial set, and the post-promotion incremental group.
    facet_sets = [
        ("event_mention", "relation_mention"),
        ("assertion", "entity_mention", "event_mention",
         "preference_expression", "relation_mention"),
        ("assertion", "entity_mention", "preference_expression"),
    ]
    for facets in facet_sets:
        drafts = extractor.extract(SUMMARY, {}, facets)
        print(f"recorded {sorted(facets)} -> {len(drafts)} verified drafts")
        time.sleep(0)  # keep ordering deterministic in output

    digest = hashlib.sha256(SUMMARY.encode()).hexdigest()
    print(f"content sha256: {digest}")
    print(f"records in {RECORD_DIR}:")
    for path in sorted(RECORD_DIR.glob("*.json")):
        print(f"  {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
