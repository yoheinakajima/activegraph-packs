"""Semantic Extraction pack — the ADR 0026 slice-1 acceptance tests.

Byte-deterministic extraction; idempotent re-extraction (same cache
identity → no new annotations); facet-incremental re-extraction; version
invalidation demoting annotations and dependent candidates via provenance
while evidence stays intact; coverage recorded and queryable; and the
envelope/projector invariants that make the layer reviewable.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from activegraph import Graph, Runtime

from packs.activity_normalizer import pack as normalizer_pack
from packs.core import pack as core_pack
from packs.semantic_extraction import (
    SemanticExtractionSettings,
    pack as semantic_pack,
)
from packs.semantic_extraction.facets import (
    DEFAULT_EAGER_FLOOR,
    STANDARD_FACETS,
)
from packs.semantic_extraction.tools import (
    annotation_coverage_fn,
    extract_annotations_fn,
    invalidate_annotation_extractor_fn,
    update_extraction_profile_fn,
)

SUMMARY = (
    "Yohei Nakajima is a general partner at Untapped Capital. "
    "He created BabyAGI on 2023-03-28 and shares projects at "
    "https://yoheinakajima.com. You can reach him at yohei@untapped.vc "
    "or @yoheinakajima. He prefers building small deterministic tools. "
    "What should the agent build next? "
    "He started the activegraph project in June 2026. "
    "Maybe he might explore vector databases later."
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


def _acquire(graph, *, text: str = SUMMARY, dedup_key: str = "summary-1",
             role: str = "assistant", provider_time: str = "2026-07-10T00:00:00Z"):
    digest = hashlib.sha256(text.encode()).hexdigest()
    item = graph.add_object(
        "acquired_item",
        {
            "source_surface_id": "surface_semantic_test",
            "provider_item_id": dedup_key,
            "dedup_key": dedup_key,
            "source_ref": f"test:{dedup_key}",
            "source_hash": digest,
            "provider_time": provider_time,
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
            "normalized_metadata": {"role": role},
            "source_category": "ai_activity",
            "connection_path": "pack",
            "is_fixture": True,
        },
    )


def _annotation_fingerprint(graph) -> str:
    entries = []
    for annotation in graph.objects(type="semantic_annotation"):
        data = annotation.data
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
            }
        )
    entries.sort(key=lambda entry: entry["identity"])
    return json.dumps(entries, sort_keys=True)


# ------------------------------------------------------------ determinism


def test_extraction_is_byte_deterministic():
    fingerprints = []
    for _ in range(2):
        graph, runtime = _build()
        _acquire(graph)
        runtime.run_until_idle()
        fingerprints.append(_annotation_fingerprint(graph))
    assert fingerprints[0] == fingerprints[1]
    assert fingerprints[0] != "[]"


def test_every_annotation_carries_the_full_envelope():
    graph, runtime = _build()
    _acquire(graph)
    runtime.run_until_idle()
    annotations = graph.objects(type="semantic_annotation")
    assert annotations
    evidence = graph.objects(type="activity_evidence")[0]
    for annotation in annotations:
        data = annotation.data
        assert data["evidence_id"] == evidence.id
        assert data["evidence_identity"] == evidence.data["evidence_identity"]
        assert data["revision_id"] == evidence.data["revision_id"]
        selector = data["selector"]
        assert selector["kind"] == "char_span"
        exact = evidence.data["normalized_content"][selector["start"]:selector["end"]]
        assert exact == selector["exact"], (data["facet"], exact, selector)
        assert data["extractor_id"] == "semantic.deterministic"
        assert data["extractor_version"] == "0.1.0"
        assert len(data["config_hash"]) == 64
        assert 0.0 <= data["confidence"] <= 1.0
        assert data["attribution"] == "author_about_subject"
        assert data["observation_time"] == "2026-07-10T00:00:00Z"
        assert data["status"] == "active"
        assert data["run_id"]


def test_modality_polarity_and_event_time():
    graph, runtime = _build()
    _acquire(graph)
    runtime.run_until_idle()
    annotations = graph.objects(type="semantic_annotation")
    uncertain = [
        annotation
        for annotation in annotations
        if annotation.data["modality"] == "uncertain"
    ]
    assert uncertain, "hedged sentence should be modality=uncertain"
    temporal = [
        annotation
        for annotation in annotations
        if annotation.data["facet"] == "temporal_expression"
    ]
    assert temporal
    assert all(
        annotation.data["event_time"] == annotation.data["body"]["normalized"]
        for annotation in temporal
    )


def test_subject_attribution_for_user_role():
    graph, runtime = _build()
    _acquire(graph, role="user")
    runtime.run_until_idle()
    attributions = {
        annotation.data["attribution"]
        for annotation in graph.objects(type="semantic_annotation")
    }
    assert attributions == {"subject_self"}


# ------------------------------------------------------------ cache identity


def test_idempotent_reextraction_same_cache_identity():
    graph, runtime = _build()
    _acquire(graph)
    runtime.run_until_idle()
    annotations = len(graph.objects(type="semantic_annotation"))
    runs = len(graph.objects(type="extraction_run"))

    evidence = graph.objects(type="activity_evidence")[0]
    result = extract_annotations_fn(graph, evidence.id)
    runtime.run_until_idle()
    assert result["created"] is False
    assert len(graph.objects(type="semantic_annotation")) == annotations
    assert len(graph.objects(type="extraction_run")) == runs


def test_reacquisition_of_identical_content_adds_nothing():
    graph, runtime = _build()
    _acquire(graph)
    runtime.run_until_idle()
    annotations = len(graph.objects(type="semantic_annotation"))
    _acquire(graph)
    runtime.run_until_idle()
    assert len(graph.objects(type="activity_evidence")) == 1
    assert len(graph.objects(type="semantic_annotation")) == annotations


def test_facet_incremental_reextraction_fills_only_missing():
    graph, runtime = _build()
    _acquire(graph)
    runtime.run_until_idle()
    before = len(graph.objects(type="semantic_annotation"))
    evidence = graph.objects(type="activity_evidence")[0]

    result = extract_annotations_fn(
        graph, evidence.id, facets=list(DEFAULT_EAGER_FLOOR) + ["topic_tag"]
    )
    runtime.run_until_idle()
    assert result["created"] is True
    assert result["executed_facets"] == ["topic_tag"]
    assert sorted(result["cached_facets"]) == sorted(DEFAULT_EAGER_FLOOR)
    new_annotations = graph.objects(type="semantic_annotation")
    assert all(
        annotation.data["facet"] == "topic_tag"
        for annotation in new_annotations[before:]
    )
    assert len(new_annotations) > before


def test_unimplemented_standard_facet_is_recorded_not_silently_claimed():
    graph, runtime = _build()
    _acquire(graph)
    runtime.run_until_idle()
    evidence = graph.objects(type="activity_evidence")[0]
    result = extract_annotations_fn(graph, evidence.id, facets=["idea"])
    runtime.run_until_idle()
    assert result["executed_facets"] == []
    coverage = annotation_coverage_fn(graph, evidence_id=evidence.id)
    reasons = {
        (entry["facet"], entry["reason"])
        for record in coverage
        for entry in record["skipped_facets"]
    }
    assert ("idea", "not_implemented") in reasons


def test_new_revision_gets_fresh_annotations():
    graph, runtime = _build()
    _acquire(graph)
    runtime.run_until_idle()
    first = len(graph.objects(type="semantic_annotation"))
    _acquire(graph, text=SUMMARY + " He also mentors founders every week.")
    runtime.run_until_idle()
    evidences = graph.objects(type="activity_evidence")
    assert len(evidences) == 2
    statuses = {evidence.data["status"] for evidence in evidences}
    assert statuses == {"current", "superseded"}
    assert len(graph.objects(type="semantic_annotation")) > first


# ------------------------------------------------------------ coverage


def test_coverage_recorded_and_queryable():
    graph, runtime = _build()
    _acquire(graph)
    runtime.run_until_idle()
    evidence = graph.objects(type="activity_evidence")[0]
    records = annotation_coverage_fn(graph, evidence_id=evidence.id)
    assert len(records) == 1
    record = records[0]
    assert sorted(record["processed_facets"]) == sorted(DEFAULT_EAGER_FLOOR)
    assert record["content_chars_total"] == len(SUMMARY)
    assert record["content_chars_processed"] == len(SUMMARY)
    assert record["truncated"] is False
    run = graph.get_object(record["run_id"])
    assert run.data["run_identity"]


def test_truncation_is_visible_in_coverage():
    settings = SemanticExtractionSettings(max_content_chars=1_000)
    graph, runtime = _build(settings)
    _acquire(graph, text=SUMMARY + " filler" * 400)
    runtime.run_until_idle()
    records = annotation_coverage_fn(graph)
    assert records[0]["truncated"] is True
    assert records[0]["content_chars_processed"] == 1_000


# ------------------------------------------------------------ config artifact


def test_profile_seeded_once_and_versioned_update_supersedes():
    graph, runtime = _build()
    runtime.run_until_idle()
    profiles = graph.objects(type="extraction_profile")
    assert len(profiles) == 1
    assert profiles[0].data["version"] == 1
    assert sorted(profiles[0].data["default_facets"]) == sorted(DEFAULT_EAGER_FLOOR)

    result = update_extraction_profile_fn(
        graph,
        facets_by_source_category={"ai_activity": ["assertion", "topic_tag"]},
        rationale="narrow ai_activity",
    )
    runtime.run_until_idle()
    assert result["version"] == 2
    profiles = sorted(
        graph.objects(type="extraction_profile"),
        key=lambda obj: obj.data["version"],
    )
    assert [profile.data["status"] for profile in profiles] == [
        "superseded",
        "active",
    ]

    _acquire(graph)
    runtime.run_until_idle()
    run = graph.objects(type="extraction_run")[0]
    assert run.data["requested_facets"] == ["assertion", "topic_tag"]


def test_profile_update_rejects_unknown_facets():
    graph, runtime = _build()
    runtime.run_until_idle()
    with pytest.raises(ValueError, match="unknown facet"):
        update_extraction_profile_fn(graph, default_facets=["vibes"])


# ------------------------------------------------------------ invalidation


def test_version_invalidation_demotes_via_provenance_evidence_intact():
    graph, runtime = _build()
    _acquire(graph)
    runtime.run_until_idle()
    evidence_ids = {evidence.id for evidence in graph.objects(type="activity_evidence")}
    profile_candidates = [
        candidate
        for candidate in graph.objects(type="profile_candidate")
        if (candidate.data.get("metadata") or {}).get("projector")
        == "semantic_extraction.profile"
    ]
    assert profile_candidates

    result = invalidate_annotation_extractor_fn(
        graph, "semantic.deterministic", "0.1.0", reason="bad heuristic"
    )
    runtime.run_until_idle()
    assert result["invalidated_annotations"] > 0
    assert result["demoted_profile_candidates"] == len(profile_candidates)

    for annotation in graph.objects(type="semantic_annotation"):
        assert annotation.data["status"] == "invalidated"
        assert annotation.data["invalidation_reason"] == "bad heuristic"
    for candidate in graph.objects(type="profile_candidate"):
        if (candidate.data.get("metadata") or {}).get("projector") == (
            "semantic_extraction.profile"
        ):
            assert candidate.data["status"] == "invalidated"
    for candidate in graph.objects(type="memory_candidate"):
        if candidate.data.get("observation_ids"):
            assert candidate.data["accepted"] is False
            assert candidate.data["confidence"] == 0.0

    surviving = {evidence.id for evidence in graph.objects(type="activity_evidence")}
    assert surviving == evidence_ids
    assert all(
        evidence.data["status"] == "current"
        for evidence in graph.objects(type="activity_evidence")
    )


def test_disabled_extractor_blocks_new_runs():
    graph, runtime = _build()
    _acquire(graph)
    runtime.run_until_idle()
    invalidate_annotation_extractor_fn(
        graph, "semantic.deterministic", "0.1.0", reason="disabled"
    )
    _acquire(graph, text="Another summary about Tokyo on 2026-01-02.",
             dedup_key="summary-2")
    runtime.run_until_idle()
    runs = [
        run
        for run in graph.objects(type="extraction_run")
        if run.data["status"] == "completed"
    ]
    assert not runs, "disabled extractor must not produce new completed runs"


# ------------------------------------------------------------ layer invariants


def test_extraction_run_produces_annotations_never_candidates():
    """ADR 0026 rule 6: candidates come from projectors, not extraction."""
    graph, runtime = _build(
        SemanticExtractionSettings(
            mint_profile_candidates=False, mint_memory_candidates=False
        )
    )
    _acquire(graph)
    runtime.run_until_idle()
    assert graph.objects(type="semantic_annotation")
    for candidate in graph.objects(type="profile_candidate"):
        metadata = candidate.data.get("metadata") or {}
        assert metadata.get("projector") != "semantic_extraction.profile"
    for candidate in graph.objects(type="memory_candidate"):
        assert not candidate.data.get("observation_ids")


def test_candidates_chain_to_annotations_and_evidence():
    graph, runtime = _build()
    _acquire(graph)
    runtime.run_until_idle()
    relations = {
        (relation.source, relation.target)
        for relation in graph.relations(type="projected_from_annotation")
    }
    assert relations
    annotation_ids = {
        annotation.id for annotation in graph.objects(type="semantic_annotation")
    }
    for _candidate_id, annotation_id in relations:
        assert annotation_id in annotation_ids


def test_standard_facet_set_is_complete():
    assert STANDARD_FACETS == (
        "entity_mention",
        "assertion",
        "question",
        "idea",
        "event_mention",
        "relation_mention",
        "preference_expression",
        "temporal_expression",
        "quantity_mention",
        "topic_tag",
    )
