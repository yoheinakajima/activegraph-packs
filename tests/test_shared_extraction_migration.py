"""ADR 0026 steps 2-4: packs write paths on the shared extraction layer.

The migration invariants:

- The activity-normalizer's direct evidence→candidate write path is
  disabled; the structure extractor emits annotations and compatibility
  projectors mint the same candidate types with the legacy identity
  scheme.
- Entity-mention ownership lives in the extraction contract; the entity
  pack consumes entity_mention annotations and keeps sole ownership of
  canonical resolution; its raw-source extraction path is disabled.
- Idempotency across the migration boundary: re-running extraction over
  evidence a pre-migration graph already extracted creates no new
  legacy-kind candidates, no duplicate annotations, and no changes to
  the candidate projection (byte-level comparison).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from activegraph import Graph, Runtime

from packs.activity_normalizer import (
    ActivityNormalizerSettings,
    pack as normalizer_pack,
)
from packs.core import pack as core_pack
from packs.entity import EntitySettings, pack as entity_pack
from packs.entity.behaviors import clear_entity_registry
from packs.semantic_extraction import pack as semantic_pack
from packs.semantic_extraction.tools import extract_annotations_fn

CONTENT = (
    "Remember: Yohei ships small tools.\n"
    "Preference: concise status updates.\n"
    "TODO: publish the migration notes.\n"
    "Skill: daily_repo_digest\n"
    "Eval: the summary helped a lot.\n"
    "My name is Yohei.\n"
)
DIGEST = hashlib.sha256(CONTENT.encode()).hexdigest()

LEGACY_CANDIDATE_TYPES = (
    "preference_candidate",
    "task_candidate",
    "profile_candidate",
    "skill_candidate",
    "eval_candidate",
)


def _acquire(graph, dedup_key: str = "migration-1", text: str = CONTENT) -> None:
    digest = hashlib.sha256(text.encode()).hexdigest()
    item = graph.add_object(
        "acquired_item",
        {
            "source_surface_id": "surface_migration_test",
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
            "normalized_metadata": {"role": "user"},
            "source_category": "ai_activity",
            "connection_path": "pack",
            "is_fixture": True,
        },
    )


def _is_semantic_profile(data) -> bool:
    """A profile_candidate minted by the semantic_extraction projector
    (from standard facets), not the legacy/compat structure path."""
    return (data.get("metadata") or {}).get("projector") == (
        "semantic_extraction.profile"
    )


def _memory_is_structure(graph, data) -> bool:
    """A memory_candidate belonging to the legacy structure semantics:
    the direct write (no observation ids) or the compat projection over
    an activity.memory annotation — never the semantic_extraction
    projector's (which observes a standard `assertion` annotation)."""
    observation_ids = data.get("observation_ids") or []
    if not observation_ids:
        return True
    return any(
        (graph.get_object(oid) is not None)
        and graph.get_object(oid).data.get("facet") == "activity.memory"
        for oid in observation_ids
    )


def _legacy_candidate_projection(graph) -> str:
    """Byte-level projection of everything downstream consumes from the
    legacy candidate kinds: identity, kind, text, status, domain fields.

    Isolated to the legacy structure candidates (direct write OR compat
    projector) — the semantic_extraction projectors' profile/memory
    candidates are a separate, additive concern and are excluded.
    Deliberately excludes extractor provenance fields, which honestly
    differ across the boundary (direct write vs annotation projector)."""
    entries = []
    for candidate_type in LEGACY_CANDIDATE_TYPES:
        for candidate in graph.objects(type=candidate_type):
            data = candidate.data
            if candidate_type == "profile_candidate" and _is_semantic_profile(data):
                continue
            entries.append(
                {
                    "type": candidate_type,
                    "candidate_identity": data.get("candidate_identity"),
                    "text": data.get("text"),
                    "confidence": data.get("confidence"),
                    "status": data.get("status"),
                    "domain_fields": {
                        key: data[key]
                        for key in (
                            "preference", "title", "attribute", "value",
                            "name", "description", "subject", "judgment",
                        )
                        if key in data
                    },
                }
            )
    for candidate in graph.objects(type="memory_candidate"):
        data = candidate.data
        if not _memory_is_structure(graph, data):
            continue
        entries.append(
            {
                "type": "memory_candidate",
                "candidate_identity": None,
                "text": data.get("text"),
                "confidence": data.get("confidence"),
                "status": "candidate",
                "domain_fields": {"category": data.get("category")},
            }
        )
    entries.sort(
        key=lambda entry: (entry["type"], entry["text"] or "", entry["candidate_identity"] or "")
    )
    return json.dumps(entries, sort_keys=True)


def _annotation_identities(graph) -> list[str]:
    return sorted(
        annotation.data["annotation_identity"]
        for annotation in graph.objects(type="semantic_annotation")
    )


def _build_migrated(graph=None):
    graph = graph or Graph()
    runtime = Runtime(graph)
    runtime.load_pack(core_pack)
    runtime.load_pack(normalizer_pack)
    runtime.load_pack(semantic_pack)
    return graph, runtime


# ---------------------------------------------------- the migrated flow


def test_direct_write_path_is_disabled_and_annotations_flow():
    graph, runtime = _build_migrated()
    _acquire(graph)
    runtime.run_until_idle()

    # No extraction_record: the direct write path never ran.
    assert not graph.objects(type="extraction_record")

    facets = {
        annotation.data["facet"]
        for annotation in graph.objects(type="semantic_annotation")
    }
    assert {
        "activity.memory", "activity.preference", "activity.task",
        "activity.profile", "activity.skill", "activity.eval",
    } <= facets, facets

    # The compat projectors minted every legacy candidate kind. (For
    # profile_candidate, semantic_extraction's own projector also mints
    # from standard facets — isolate the compat ones by projector tag.)
    for candidate_type in LEGACY_CANDIDATE_TYPES:
        compat = [
            candidate
            for candidate in graph.objects(type=candidate_type)
            if (candidate.data.get("metadata") or {}).get("projector")
            == "activity_normalizer.compat"
        ]
        assert compat, candidate_type
        for candidate in compat:
            assert candidate.data["status"] == "candidate"
            assert candidate.data["candidate_identity"]
            metadata = candidate.data["metadata"]
            # … each walkable back to its annotation and evidence.
            annotation = graph.get_object(metadata["annotation_id"])
            assert annotation is not None
            assert annotation.data["facet"].startswith("activity.")

    # And every annotation selector anchors real content bytes.
    evidence = graph.objects(type="activity_evidence")[0]
    content = evidence.data["normalized_content"]
    for annotation in graph.objects(type="semantic_annotation"):
        selector = annotation.data["selector"]
        assert content[selector["start"]:selector["end"]] == selector["exact"]


def test_compat_candidates_match_legacy_identities():
    """The compat projector reproduces the exact legacy candidate
    identities, so the two paths collide instead of duplicating."""
    legacy_graph = Graph()
    legacy_runtime = Runtime(legacy_graph)
    legacy_runtime.load_pack(core_pack)
    legacy_runtime.load_pack(
        normalizer_pack,
        settings=ActivityNormalizerSettings(legacy_extraction_enabled=True),
    )
    _acquire(legacy_graph)
    legacy_runtime.run_until_idle()

    migrated_graph, migrated_runtime = _build_migrated()
    _acquire(migrated_graph)
    migrated_runtime.run_until_idle()

    for candidate_type in LEGACY_CANDIDATE_TYPES:
        legacy_ids = {
            candidate.data["candidate_identity"]
            for candidate in legacy_graph.objects(type=candidate_type)
        }
        # Isolate the compat projector's candidates (semantic_extraction
        # mints profile candidates of its own from standard facets).
        migrated_ids = {
            candidate.data["candidate_identity"]
            for candidate in migrated_graph.objects(type=candidate_type)
            if (candidate.data.get("metadata") or {}).get("projector")
            == "activity_normalizer.compat"
        }
        assert legacy_ids == migrated_ids, candidate_type
        assert legacy_ids, candidate_type


# ------------------------------------------------- idempotency proof


def test_reingestion_after_migration_is_idempotent_byte_level():
    """Re-running ingestion + explicit re-extraction over existing
    evidence adds nothing: same candidates, same annotations, byte-equal
    projection."""
    graph, runtime = _build_migrated()
    _acquire(graph)
    runtime.run_until_idle()
    projection_before = _legacy_candidate_projection(graph)
    annotations_before = _annotation_identities(graph)
    assert projection_before != "[]"

    # Re-ingest the same item and explicitly re-extract the evidence.
    _acquire(graph)
    runtime.run_until_idle()
    evidence = graph.objects(type="activity_evidence")[0]
    result = extract_annotations_fn(graph, evidence.id)
    runtime.run_until_idle()

    assert result["created"] is False
    assert _legacy_candidate_projection(graph) == projection_before
    assert _annotation_identities(graph) == annotations_before
    assert len(annotations_before) == len(set(annotations_before))


def test_migration_boundary_reextraction_creates_no_new_candidates():
    """The cross-boundary proof: a graph extracted by the LEGACY path is
    re-extracted by the MIGRATED stack — the legacy-kind candidate
    projection is byte-identical (annotations appear; candidates don't
    duplicate), and a second migrated pass adds nothing at all."""
    graph = Graph()
    legacy_runtime = Runtime(graph)
    legacy_runtime.load_pack(core_pack)
    legacy_runtime.load_pack(
        normalizer_pack,
        settings=ActivityNormalizerSettings(legacy_extraction_enabled=True),
    )
    _acquire(graph)
    legacy_runtime.run_until_idle()
    projection_before = _legacy_candidate_projection(graph)
    assert projection_before != "[]"
    assert not graph.objects(type="semantic_annotation")

    # The migration boundary: same graph, migrated stack.
    migrated_runtime = Runtime(graph)
    migrated_runtime.load_pack(core_pack)
    migrated_runtime.load_pack(normalizer_pack)
    migrated_runtime.load_pack(semantic_pack)
    migrated_runtime.run_until_idle()

    # Re-ingest the same item (normalizer dedups) and re-extract the
    # existing evidence through the shared layer.
    _acquire(graph)
    migrated_runtime.run_until_idle()
    evidence = graph.objects(type="activity_evidence")[0]
    extract_annotations_fn(graph, evidence.id)
    migrated_runtime.run_until_idle()

    assert graph.objects(type="semantic_annotation"), "annotations must appear"
    assert _legacy_candidate_projection(graph) == projection_before, (
        "re-extraction across the migration boundary must not change the "
        "candidate projection"
    )

    # And the migrated world is a fixed point: another full pass changes
    # nothing anywhere.
    projection_after = _legacy_candidate_projection(graph)
    annotations_after = _annotation_identities(graph)
    _acquire(graph)
    migrated_runtime.run_until_idle()
    extract_annotations_fn(graph, evidence.id)
    migrated_runtime.run_until_idle()
    assert _legacy_candidate_projection(graph) == projection_after
    assert _annotation_identities(graph) == annotations_after


# ------------------------------------------------- entity ownership


def test_entity_pack_consumes_annotations_and_keeps_resolution():
    clear_entity_registry()
    graph = Graph()
    runtime = Runtime(graph)
    runtime.load_pack(core_pack)
    runtime.load_pack(normalizer_pack)
    runtime.load_pack(semantic_pack)
    runtime.load_pack(entity_pack, settings=EntitySettings())
    try:
        _acquire(
            graph,
            dedup_key="entity-1",
            text=(
                "Yohei Nakajima met the team at Untapped Capital. "
                "Reach him at yohei@untapped.vc."
            ),
        )
        runtime.run_until_idle()

        mentions = graph.objects(type="entity_mention")
        assert mentions, "annotation-consuming path must mint mentions"
        assert all(
            mention.data["extraction_method"] == "annotation"
            for mention in mentions
        )
        annotation_ids = {
            mention.data["metadata"]["annotation_id"] for mention in mentions
        }
        assert len(annotation_ids) == len(mentions), "one mention per annotation"

        # Canonical resolution stayed in the entity pack: every mention
        # resolved to a canonical entity id.
        assert all(mention.data["entity_id"] for mention in mentions)
        entities = graph.objects(type="entity")
        assert entities

        # Idempotency: re-running extraction mints nothing new.
        before = len(mentions), len(entities)
        evidence = graph.objects(type="activity_evidence")[0]
        extract_annotations_fn(graph, evidence.id)
        runtime.run_until_idle()
        assert (
            len(graph.objects(type="entity_mention")),
            len(graph.objects(type="entity")),
        ) == before
    finally:
        clear_entity_registry()


def test_entity_raw_source_extraction_is_disabled_by_default():
    clear_entity_registry()
    graph = Graph()
    runtime = Runtime(graph)
    runtime.load_pack(core_pack)
    runtime.load_pack(entity_pack, settings=EntitySettings())
    try:
        graph.add_object(
            "source",
            {
                "content": "Yohei Nakajima works with Untapped Capital.",
                "kind": "note",
            },
        )
        runtime.run_until_idle()
        assert not graph.objects(type="entity_mention"), (
            "the duplicate raw-source extraction path must be off by default"
        )
    finally:
        clear_entity_registry()
