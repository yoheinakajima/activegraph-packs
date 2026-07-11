"""Deterministic offline fixtures for the Semantic Extraction pack.

No network, no keys, no wall-clock. Exercises the full slice: eager
annotation under the seeded profile, candidate projection, idempotent
re-acquisition, facet-incremental re-extraction, coverage, and extractor
version invalidation demoting through provenance.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parents[2]))

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
from packs.semantic_extraction.tools import (
    annotation_coverage_fn,
    extract_annotations_fn,
    invalidate_annotation_extractor_fn,
    promote_llm_extractor_fn,
    run_extractor_trial_fn,
)

SUMMARY = (
    "Yohei Nakajima is a general partner at Untapped Capital. "
    "He created BabyAGI on 2023-03-28 and shares projects at "
    "https://yoheinakajima.com. You can reach him at yohei@untapped.vc "
    "or @yoheinakajima. He prefers building small deterministic tools. "
    "What should the agent build next? "
    "He started the activegraph project in June 2026."
)
DIGEST = hashlib.sha256(SUMMARY.encode()).hexdigest()


def _acquire(graph) -> None:
    item = graph.add_object(
        "acquired_item",
        {
            "source_surface_id": "surface_semantic_fixture",
            "provider_item_id": "summary-1",
            "dedup_key": "summary-1",
            "source_ref": "fixture:summary-1",
            "source_hash": DIGEST,
            "provider_time": "2026-07-10T00:00:00Z",
            "replay_mode": "inline",
            "replay_payload_ref": SUMMARY,
            "replay_payload_hash": DIGEST,
            "media_type": "text/plain",
            "importer_id": "fixture",
            "importer_version": "0.1.0",
        },
    )
    graph.add_object(
        "acquired_content",
        {
            "acquired_item_id": item.id,
            "normalized_content": SUMMARY,
            "normalized_metadata": {"role": "assistant"},
            "source_category": "ai_activity",
            "connection_path": "pack",
            "is_fixture": True,
        },
    )


def _build() -> tuple[Graph, Runtime]:
    graph = Graph()
    runtime = Runtime(graph)
    runtime.load_pack(core_pack)
    runtime.load_pack(normalizer_pack)
    runtime.load_pack(semantic_pack)
    return graph, runtime


def run_eager_annotation_fixture() -> dict:
    graph, runtime = _build()
    runtime.run_until_idle()
    profiles = sorted(
        graph.objects(type="extraction_profile"),
        key=lambda obj: obj.data["version"],
    )
    # v1 seed + v2 (the normalizer's shared-path selection, ADR 0026).
    assert [p.data["version"] for p in profiles] == [1, 2], "profile not seeded"
    assert [p.data["status"] for p in profiles] == ["superseded", "active"]
    assert all(
        ref == "activity.structure@0.2.0"
        for ref in profiles[1].data["extractor_by_facet"].values()
    )

    _acquire(graph)
    runtime.run_until_idle()

    annotations = graph.objects(type="semantic_annotation")
    facets = {annotation.data["facet"] for annotation in annotations}
    assert "entity_mention" in facets, facets
    assert "assertion" in facets, facets
    assert "preference_expression" in facets, facets
    assert "question" in facets, facets
    assert "temporal_expression" in facets, facets

    kinds = {
        annotation.data["body"].get("kind")
        for annotation in annotations
        if annotation.data["facet"] == "entity_mention"
    }
    assert {"email", "url", "handle", "proper_noun"} <= kinds, kinds

    dates = {
        annotation.data["body"]["normalized"]
        for annotation in annotations
        if annotation.data["facet"] == "temporal_expression"
    }
    assert "2023-03-28" in dates and "2026-06" in dates, dates

    runs = graph.objects(type="extraction_run")
    assert {run.data["extractor_id"] for run in runs} == {
        "semantic.deterministic",
        "activity.structure",
    }
    coverage = annotation_coverage_fn(graph)
    assert len(coverage) == len(runs)
    assert all(record["processed_facets"] for record in coverage), coverage

    profile_candidates = graph.objects(type="profile_candidate")
    assert profile_candidates, "no profile candidates projected"
    memory_candidates = graph.objects(type="memory_candidate")
    annotation_backed = [
        candidate
        for candidate in memory_candidates
        if candidate.data.get("observation_ids")
    ]
    assert annotation_backed, "no annotation-backed memory candidates"
    return {
        "annotations": len(annotations),
        "profile_candidates": len(profile_candidates),
        "memory_candidates": len(annotation_backed),
    }


def run_idempotent_reextraction_fixture() -> dict:
    graph, runtime = _build()
    _acquire(graph)
    runtime.run_until_idle()
    annotation_count = len(graph.objects(type="semantic_annotation"))
    run_count = len(graph.objects(type="extraction_run"))

    _acquire(graph)  # same dedup key + content: normalizer dedups, no new revision
    runtime.run_until_idle()
    assert len(graph.objects(type="semantic_annotation")) == annotation_count
    assert len(graph.objects(type="extraction_run")) == run_count

    evidence = graph.objects(type="activity_evidence")[0]
    result = extract_annotations_fn(graph, evidence.id)
    runtime.run_until_idle()
    assert result["created"] is False, "same cache identity must be a no-op"
    assert len(graph.objects(type="semantic_annotation")) == annotation_count
    return {"annotations": annotation_count, "stable": True}


def run_facet_incremental_fixture() -> dict:
    graph, runtime = _build()
    _acquire(graph)
    runtime.run_until_idle()
    evidence = graph.objects(type="activity_evidence")[0]
    before = len(graph.objects(type="semantic_annotation"))

    result = extract_annotations_fn(
        graph,
        evidence.id,
        facets=[
            "assertion",
            "entity_mention",
            "preference_expression",
            "question",
            "temporal_expression",
            "topic_tag",
        ],
    )
    runtime.run_until_idle()
    assert result["created"] is True
    assert result["executed_facets"] == ["topic_tag"], result
    assert set(result["cached_facets"]) == {
        "assertion",
        "entity_mention",
        "preference_expression",
        "question",
        "temporal_expression",
    }, result
    after = graph.objects(type="semantic_annotation")
    new = [annotation for annotation in after if annotation.data["facet"] == "topic_tag"]
    assert len(after) == before + len(new)
    assert new, "topic_tag facet produced nothing"
    coverage = annotation_coverage_fn(graph, evidence_id=evidence.id)
    cached_reasons = {
        entry["reason"]
        for record in coverage
        for entry in record["skipped_facets"]
    }
    assert "cached" in cached_reasons, coverage
    return {"incremental_annotations": len(new)}


def run_invalidation_fixture() -> dict:
    graph, runtime = _build()
    _acquire(graph)
    runtime.run_until_idle()
    evidence_before = len(graph.objects(type="activity_evidence"))

    result = invalidate_annotation_extractor_fn(
        graph, "semantic.deterministic", "0.1.0", reason="fixture invalidation"
    )
    runtime.run_until_idle()
    assert result["invalidated_annotations"] > 0
    assert result["demoted_profile_candidates"] > 0

    statuses = {
        annotation.data["extractor_id"]: annotation.data["status"]
        for annotation in graph.objects(type="semantic_annotation")
    }
    # Only the invalidated extractor identity's annotations demote; the
    # structure emitter's stay active (different extractor identity).
    assert statuses["semantic.deterministic"] == "invalidated", statuses
    assert statuses.get("activity.structure", "active") == "active", statuses
    candidate_statuses = {
        candidate.data["status"]
        for candidate in graph.objects(type="profile_candidate")
        if (candidate.data.get("metadata") or {}).get("projector")
        == "semantic_extraction.profile"
    }
    assert candidate_statuses == {"invalidated"}, candidate_statuses
    assert len(graph.objects(type="activity_evidence")) == evidence_before
    assert all(
        evidence.data["status"] == "current"
        for evidence in graph.objects(type="activity_evidence")
    ), "evidence must stay intact"
    return dict(result)


class _PoisonProvider:
    """A 'live' provider that must never be reached: the fixture replays
    entirely from the committed records (zero keys, zero network)."""

    default_model = "fixture-llm-1"

    def complete(self, **kwargs):
        raise AssertionError(
            "fixture made a live provider call — records must cover "
            "every prompt"
        )


def run_llm_upgrade_trial_fixture() -> dict:
    """D025 stage two on records: provider-configured seed routes the two
    LLM-only facets to semantic.llm; extraction replays from the record;
    the deterministic-vs-LLM trial lands as promotion evidence; explicit
    promotion re-routes the trialed facets."""
    clear_llm_provider()
    set_llm_provider(
        _PoisonProvider(),
        ResolvedLLMProvider(
            provider="anthropic",
            source="setting",
            api_key_env="ANTHROPIC_API_KEY",
            model="fixture-llm-1",
        ),
    )
    settings = SemanticExtractionSettings(
        llm_model="fixture-llm-1",
        llm_record_dir=str(_HERE / "llm_records"),
    )
    try:
        graph = Graph()
        runtime = Runtime(graph)
        runtime.load_pack(core_pack)
        runtime.load_pack(normalizer_pack)
        runtime.load_pack(semantic_pack, settings=settings)
        runtime.run_until_idle()

        (profile,) = [
            p for p in graph.objects(type="extraction_profile")
            if p.data["status"] == "active"
        ]
        routed = profile.data["extractor_by_facet"]
        assert routed["event_mention"] == "semantic.llm@0.1.0", routed
        assert routed["relation_mention"] == "semantic.llm@0.1.0", routed
        assert "assertion" not in routed, routed
        states = graph.objects(type="annotation_extractor_state")
        assert any(
            state.data["extractor_id"] == "semantic.llm"
            and state.data["status"] == "candidate"
            for state in states
        ), "semantic.llm must land as a candidate configuration (ADR 0014)"

        _acquire(graph)
        runtime.run_until_idle()

        annotations = graph.objects(type="semantic_annotation")
        by_extractor: dict[str, set] = {}
        for annotation in annotations:
            by_extractor.setdefault(
                annotation.data["extractor_id"], set()
            ).add(annotation.data["facet"])
        assert by_extractor["semantic.deterministic"] >= {
            "assertion", "entity_mention", "preference_expression",
            "question", "temporal_expression",
        }, by_extractor
        assert by_extractor["semantic.llm"] == {
            "event_mention", "relation_mention",
        }, by_extractor
        evidence = graph.objects(type="activity_evidence")[0]
        content = evidence.data["normalized_content"]
        for annotation in annotations:
            selector = annotation.data["selector"]
            assert (
                content[selector["start"]:selector["end"]] == selector["exact"]
            ), annotation.data

        trial = run_extractor_trial_fn(
            graph, [evidence.id], settings=settings, created_by="fixture"
        )
        assert trial["verdict"] == "candidate_richer", trial
        comparison = trial["comparison"]
        assert comparison["relation_mention"]["baseline"] == 0
        assert comparison["relation_mention"]["candidate"] > 0
        assert comparison["event_mention"]["baseline"] == 0
        assert comparison["event_mention"]["candidate"] > 0

        promoted = promote_llm_extractor_fn(
            graph, trial["evidence_id"], approver="fixture-owner"
        )
        assert promoted["ok"], promoted
        active = [
            profile
            for profile in graph.objects(type="extraction_profile")
            if profile.data["status"] == "active"
        ]
        assert len(active) == 1
        routed = active[0].data["extractor_by_facet"]
        assert routed["assertion"] == "semantic.llm@0.1.0", routed

        before = len(graph.objects(type="semantic_annotation"))
        result = extract_annotations_fn(graph, evidence.id, settings=settings)
        runtime.run_until_idle()
        after = len(graph.objects(type="semantic_annotation"))
        assert result["created"] is True
        assert after > before, "promoted routing must add LLM floor annotations"
        return {
            "llm_annotations": len(
                [a for a in graph.objects(type="semantic_annotation")
                 if a.data["extractor_id"] == "semantic.llm"]
            ),
            "verdict": trial["verdict"],
            "post_promotion_added": after - before,
        }
    finally:
        clear_llm_provider()


def run_all() -> bool:
    print("Semantic Extraction Fixtures")
    print("=" * 60)
    print(f"  [1] eager annotation      PASS: {run_eager_annotation_fixture()}")
    print(f"  [2] idempotent re-run     PASS: {run_idempotent_reextraction_fixture()}")
    print(f"  [3] facet-incremental     PASS: {run_facet_incremental_fixture()}")
    print(f"  [4] version invalidation  PASS: {run_invalidation_fixture()}")
    print(f"  [5] llm upgrade + trial   PASS: {run_llm_upgrade_trial_fixture()}")
    print("ALL PASS")
    return True


if __name__ == "__main__":
    # Fixtures are keyless by doctrine: an API key in the invoking shell
    # must not change what they exercise (fixture [5] installs its own
    # recorded provider explicitly).
    import os

    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("OPENAI_API_KEY", None)
    clear_llm_provider()
    try:
        ok = run_all()
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)
    sys.exit(0 if ok else 1)
