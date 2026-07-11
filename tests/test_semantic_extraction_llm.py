"""semantic.llm@0.1.0 — the LLM-backed extractor at the declared seam
(D025 stage two, Part 2).

The properties this part exists for: the recorded provider seam (replay
is byte-equal and never re-contacts), byte-for-byte selector
verification (an LLM may not mint an annotation whose anchor doesn't
exist), the provider-conditional default profile (no provider →
byte-identical to the deterministic-only path), and identical
candidate/promotion gates for LLM-derived annotations.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import pytest

from activegraph import Graph, Runtime

from packs.activity_normalizer import pack as normalizer_pack
from packs.core import pack as core_pack
from packs.llm_provider import (
    ResolvedLLMProvider,
    clear_llm_provider,
    set_llm_provider,
)
from packs.semantic_extraction import (
    SemanticExtractionSettings,
    pack as semantic_pack,
)
from packs.semantic_extraction.extractor import get_annotation_extractor
from packs.semantic_extraction.fixtures.seed_llm_records import (
    SUMMARY,
    ScriptedProvider,
)
from packs.semantic_extraction.llm_extractor import (
    LLMExtractorV1,
    ReplayFirstProvider,
)
from packs.semantic_extraction.tools import (
    extract_annotations_fn,
    invalidate_annotation_extractor_fn,
    promote_llm_extractor_fn,
    run_extractor_trial_fn,
)

FIXTURE_RESOLVED = ResolvedLLMProvider(
    provider="anthropic",
    source="setting",
    api_key_env="ANTHROPIC_API_KEY",
    model="fixture-llm-1",
)


class PoisonProvider:
    """A 'live' provider that must never be contacted."""

    default_model = "fixture-llm-1"

    def complete(self, **kwargs):
        raise AssertionError("live provider was contacted during replay")


@pytest.fixture(autouse=True)
def _clean_provider():
    clear_llm_provider()
    yield
    clear_llm_provider()


def _settings(record_dir: Path) -> SemanticExtractionSettings:
    return SemanticExtractionSettings(
        llm_model="fixture-llm-1", llm_record_dir=str(record_dir)
    )


def _build(settings: SemanticExtractionSettings | None = None):
    graph = Graph()
    runtime = Runtime(graph)
    runtime.load_pack(core_pack)
    runtime.load_pack(normalizer_pack)
    if settings is None:
        runtime.load_pack(semantic_pack)
    else:
        runtime.load_pack(semantic_pack, settings=settings)
    return graph, runtime


def _acquire(graph, *, text: str = SUMMARY, dedup_key: str = "summary-1"):
    digest = hashlib.sha256(text.encode()).hexdigest()
    item = graph.add_object(
        "acquired_item",
        {
            "source_surface_id": "surface_llm_test",
            "provider_item_id": dedup_key,
            "dedup_key": dedup_key,
            "source_ref": f"test:{dedup_key}",
            "source_hash": digest,
            "provider_time": "2026-07-10T00:00:00Z",
            "replay_mode": "inline",
            "replay_payload_ref": text,
            "replay_payload_hash": digest,
            "media_type": "text/plain",
            "importer_id": "test",
            "importer_version": "0.1.0",
        },
    )
    graph.add_object(
        "acquired_content",
        {
            "acquired_item_id": item.id,
            "normalized_content": text,
            "normalized_metadata": {"role": "assistant"},
            "source_category": "ai_activity",
            "connection_path": "pack",
            "is_fixture": True,
        },
    )


def _annotation_projection(graph, extractor_id: str | None = None) -> str:
    """Canonical, graph-id-free serialization for byte-equality checks."""
    entries = []
    for annotation in graph.objects(type="semantic_annotation"):
        data = annotation.data
        if extractor_id is not None and data["extractor_id"] != extractor_id:
            continue
        entries.append(
            {
                "identity": data["annotation_identity"],
                "facet": data["facet"],
                "body": data["body"],
                "selector": data["selector"],
                "confidence": data["confidence"],
                "attribution": data["attribution"],
                "modality": data["modality"],
                "polarity": data["polarity"],
                "event_time": data["event_time"],
                "observation_time": data["observation_time"],
                "extractor_id": data["extractor_id"],
                "extractor_version": data["extractor_version"],
                "config_hash": data["config_hash"],
                "metadata": data["metadata"],
            }
        )
    entries.sort(key=lambda entry: entry["identity"])
    return json.dumps(entries, sort_keys=True)


# ------------------------------------------------- registry + seam


def test_llm_extractor_registers_beside_the_deterministic_one(tmp_path):
    set_llm_provider(ScriptedProvider(), FIXTURE_RESOLVED)
    settings = _settings(tmp_path / "records")
    from packs.semantic_extraction.engine import resolve_extractor

    extractor = resolve_extractor(settings, "semantic.llm@0.1.0")
    assert extractor.extractor_id == "semantic.llm"
    assert extractor.extractor_version == "0.1.0"
    assert get_annotation_extractor("semantic.llm", "0.1.0") is extractor
    deterministic = get_annotation_extractor("semantic.deterministic", "0.1.0")
    assert deterministic.extractor_id == "semantic.deterministic"


# ------------------------------------------------- selector verification


def test_llm_may_not_mint_an_annotation_whose_anchor_does_not_exist(tmp_path):
    """The scripted response includes one span that does not occur in the
    content; verification drops exactly that one and every surviving
    selector matches the content byte-for-byte."""
    provider = ReplayFirstProvider(str(tmp_path / "records"), live=ScriptedProvider())
    extractor = LLMExtractorV1(provider=provider, model="fixture-llm-1")
    facets = (
        "assertion", "entity_mention", "event_mention",
        "preference_expression", "relation_mention",
    )
    drafts = extractor.extract(SUMMARY, {}, facets)
    exacts = {draft.exact for draft in drafts}
    assert "General Partner Yohei" not in exacts, "bogus anchor must be dropped"
    assert drafts, "verified drafts expected"
    for draft in drafts:
        assert SUMMARY[draft.start:draft.end] == draft.exact
    facet_set = {draft.facet for draft in drafts}
    assert {"relation_mention", "event_mention"} <= facet_set


# ------------------------------------------------- recorded provider seam


def test_replay_produces_byte_equal_annotations_and_never_recontacts(tmp_path):
    record_dir = tmp_path / "records"
    settings = _settings(record_dir)

    # Pass 1: live-shaped — scripted provider through the recording seam.
    set_llm_provider(ScriptedProvider(), FIXTURE_RESOLVED)
    graph_a, runtime_a = _build(settings)
    _acquire(graph_a)
    runtime_a.run_until_idle()
    projection_a = _annotation_projection(graph_a, "semantic.llm")
    assert projection_a != "[]"
    assert list(record_dir.glob("*.json")), "records must be persisted"

    # Pass 2: rebuild from nothing with a poisoned live provider — every
    # call must replay from the record.
    clear_llm_provider()
    set_llm_provider(PoisonProvider(), FIXTURE_RESOLVED)
    graph_b, runtime_b = _build(settings)
    _acquire(graph_b)
    runtime_b.run_until_idle()
    projection_b = _annotation_projection(graph_b, "semantic.llm")

    assert projection_a == projection_b, "replayed annotations must be byte-equal"
    # And the whole projection (floor included) matches too.
    assert _annotation_projection(graph_a) == _annotation_projection(graph_b)


def test_reextraction_same_identity_is_a_noop_without_provider_contact(tmp_path):
    record_dir = tmp_path / "records"
    settings = _settings(record_dir)
    set_llm_provider(ScriptedProvider(), FIXTURE_RESOLVED)
    graph, runtime = _build(settings)
    _acquire(graph)
    runtime.run_until_idle()
    before = len(graph.objects(type="semantic_annotation"))

    clear_llm_provider()
    set_llm_provider(PoisonProvider(), FIXTURE_RESOLVED)
    evidence = graph.objects(type="activity_evidence")[0]
    result = extract_annotations_fn(graph, evidence.id, settings=settings)
    runtime.run_until_idle()
    assert result["created"] is False
    assert len(graph.objects(type="semantic_annotation")) == before


# ------------------------------------------------- selection policy


def test_no_provider_means_byte_identical_behavior_to_today(tmp_path):
    """Zero-key mode: the seeded profile carries no extractor upgrade and
    the produced annotations are exactly the deterministic extractor's."""
    graph, runtime = _build()
    _acquire(graph)
    runtime.run_until_idle()

    (active,) = [
        p for p in graph.objects(type="extraction_profile")
        if p.data["status"] == "active"
    ]
    # Post-migration the active profile routes the activity.* structure
    # facets — but nothing references semantic.llm without a provider.
    assert all(
        ref == "activity.structure@0.2.0"
        for ref in active.data["extractor_by_facet"].values()
    )
    extractor_ids = {
        annotation.data["extractor_id"]
        for annotation in graph.objects(type="semantic_annotation")
    }
    assert extractor_ids == {"semantic.deterministic", "activity.structure"}
    assert not any(
        state.data.get("extractor_id") == "semantic.llm"
        for state in graph.objects(type="annotation_extractor_state")
    )
    # The full projection matches a second zero-key run byte-for-byte.
    graph_2, runtime_2 = _build()
    _acquire(graph_2)
    runtime_2.run_until_idle()
    assert _annotation_projection(graph) == _annotation_projection(graph_2)


def test_provider_upgrades_default_profile_for_the_two_missing_facets(tmp_path):
    set_llm_provider(ScriptedProvider(), FIXTURE_RESOLVED)
    settings = _settings(tmp_path / "records")
    graph, runtime = _build(settings)
    _acquire(graph)
    runtime.run_until_idle()

    (active,) = [
        p for p in graph.objects(type="extraction_profile")
        if p.data["status"] == "active"
    ]
    routed = active.data["extractor_by_facet"]
    assert routed["event_mention"] == "semantic.llm@0.1.0"
    assert routed["relation_mention"] == "semantic.llm@0.1.0"
    # Nothing else routes to the LLM — the floor stands (D041).
    assert all(
        ref == "activity.structure@0.2.0"
        for facet, ref in routed.items()
        if facet not in ("event_mention", "relation_mention")
    )
    by_extractor: dict[str, set] = {}
    for annotation in graph.objects(type="semantic_annotation"):
        by_extractor.setdefault(
            annotation.data["extractor_id"], set()
        ).add(annotation.data["facet"])
    # The cheap eager floor stands (D041) — deterministic serves it.
    assert by_extractor["semantic.deterministic"] == {
        "assertion", "entity_mention", "preference_expression",
        "question", "temporal_expression",
    }
    # The LLM serves exactly the two facets the floor cannot.
    assert by_extractor["semantic.llm"] == {"event_mention", "relation_mention"}
    # One cache-identified run per extractor group.
    runs = graph.objects(type="extraction_run")
    assert {run.data["extractor_id"] for run in runs} == {
        "semantic.deterministic", "semantic.llm", "activity.structure",
    }


# ------------------------------------------------- gates + confidence


def test_llm_annotations_face_identical_projector_and_invalidation_gates(tmp_path):
    """An LLM annotation is never more trusted for being fluent: same
    projectors, same candidate status, same invalidation demotion."""
    set_llm_provider(ScriptedProvider(), FIXTURE_RESOLVED)
    settings = _settings(tmp_path / "records")
    graph, runtime = _build(settings)
    _acquire(graph)
    runtime.run_until_idle()

    llm_annotations = [
        annotation
        for annotation in graph.objects(type="semantic_annotation")
        if annotation.data["extractor_id"] == "semantic.llm"
    ]
    assert llm_annotations
    for annotation in llm_annotations:
        assert 0.0 <= annotation.data["confidence"] <= 1.0
        assert annotation.data["status"] == "active"

    # Everything projected from any annotation stays a candidate.
    for candidate in graph.objects(type="profile_candidate"):
        assert candidate.data["status"] == "candidate"

    # Invalidating the LLM extractor version demotes its annotations via
    # provenance exactly like the deterministic one — evidence intact.
    result = invalidate_annotation_extractor_fn(
        graph, "semantic.llm", "0.1.0", reason="test invalidation"
    )
    assert result["invalidated_annotations"] == len(llm_annotations)
    for annotation in graph.objects(type="semantic_annotation"):
        expected = (
            "invalidated"
            if annotation.data["extractor_id"] == "semantic.llm"
            else "active"
        )
        assert annotation.data["status"] == expected
    assert all(
        evidence.data["status"] == "current"
        for evidence in graph.objects(type="activity_evidence")
    )


def test_confidence_is_clamped_never_amplified():
    from decimal import Decimal

    from activegraph.llm import LLMResponse

    class RawProvider:
        def complete(self, **kwargs):
            raw = json.dumps(
                [
                    {
                        "facet": "assertion",
                        "exact": "He prefers building small deterministic tools.",
                        "body": {},
                        "confidence": 7.5,
                        "modality": "stated",
                        "polarity": "positive",
                    }
                ]
            )
            return LLMResponse(
                raw_text=raw, parsed=None, input_tokens=1, output_tokens=1,
                cost_usd=Decimal("0"), latency_seconds=0.0,
                model="fixture-llm-1", finish_reason="end_turn", seed=None,
                cache_hit=False, provider_meta={}, tool_calls=None,
            )

    extractor = LLMExtractorV1(provider=RawProvider(), model="fixture-llm-1")
    (draft,) = extractor.extract(SUMMARY, {}, ("assertion",))
    assert draft.confidence == 1.0


# ------------------------------------------------- fork-trial-promote


def test_trial_records_promotion_evidence_and_promotion_is_explicit(tmp_path):
    set_llm_provider(ScriptedProvider(), FIXTURE_RESOLVED)
    settings = _settings(tmp_path / "records")
    graph, runtime = _build(settings)
    _acquire(graph)
    runtime.run_until_idle()
    evidence = graph.objects(type="activity_evidence")[0]

    trial = run_extractor_trial_fn(
        graph, [evidence.id], settings=settings, created_by="test"
    )
    assert trial["verdict"] == "candidate_richer"
    (evidence_obj,) = graph.objects(type="extractor_promotion_evidence")
    data = evidence_obj.data
    assert data["candidate_extractor_id"] == "semantic.llm"
    assert data["baseline_extractor_id"] == "semantic.deterministic"
    assert set(data["facets"]) == {
        "assertion", "entity_mention", "event_mention",
        "preference_expression", "relation_mention",
    }
    assert data["comparison"]["relation_mention"]["candidate"] > 0
    assert data["comparison"]["relation_mention"]["baseline"] == 0

    # The trial changed no policy.
    (profile,) = [
        p for p in graph.objects(type="extraction_profile")
        if p.data["status"] == "active"
    ]
    routed_before = profile.data["extractor_by_facet"]
    assert routed_before["event_mention"] == "semantic.llm@0.1.0"
    assert routed_before["relation_mention"] == "semantic.llm@0.1.0"
    assert "assertion" not in routed_before

    # Promotion requires an approver and cites the evidence.
    with pytest.raises(ValueError):
        promote_llm_extractor_fn(graph, evidence_obj.id, approver="")
    promoted = promote_llm_extractor_fn(
        graph, evidence_obj.id, approver="owner"
    )
    assert promoted["ok"]
    active = [
        p for p in graph.objects(type="extraction_profile")
        if p.data["status"] == "active"
    ]
    assert len(active) == 1
    routed = active[0].data["extractor_by_facet"]
    for facet in ("assertion", "entity_mention", "preference_expression"):
        assert routed[facet] == "semantic.llm@0.1.0"
    states = [
        state.data
        for state in graph.objects(type="annotation_extractor_state")
        if state.data["extractor_id"] == "semantic.llm"
    ]
    assert any(state["status"] == "promoted" for state in states)
    assert any(state["status"] == "candidate" for state in states)
