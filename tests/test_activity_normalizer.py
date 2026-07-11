"""P1 acceptance tests for local acquisition, normalization, and replay.

These are integration tests across graph events, not direct cross-pack wiring in
production.  Importers deliberately re-emit overlapping acquisition records;
the assertions below verify that the normalizer's logical identities keep
evidence, extraction records, and candidates idempotent downstream.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from activegraph import Graph, Runtime

sys.path.insert(0, str(Path(__file__).parents[1]))

from packs.activity_normalizer import (
    ActivityNormalizerSettings,
    pack as activity_normalizer_pack,
)
from packs.activity_normalizer.tools import reextract_evidence_fn
from packs.activity_normalizer.replay import artifact_path
from packs.core import CoreSettings, pack as core_pack
from packs.importers.local_files import LocalFilesSettings, pack as local_files_pack
from packs.importers.local_files.tools import import_local_files_fn


_FIXED_FILE_TIME = 1_704_067_200  # 2024-01-01T00:00:00Z
_CANDIDATE_TYPES = (
    "memory_candidate",
    "preference_candidate",
    "task_candidate",
    "profile_candidate",
    "skill_candidate",
    "eval_candidate",
)


@dataclass
class _Pipeline:
    runtime: Runtime
    source_dir: Path
    artifact_dir: Path
    normalizer_settings: ActivityNormalizerSettings
    surface_id: str = "surface_local_acceptance"

    @property
    def graph(self):
        return self.runtime.graph

    def import_snapshot(self, *, replay_mode: str = "artifact") -> dict:
        result = import_local_files_fn(
            self.graph,
            str(self.source_dir),
            self.surface_id,
            artifact_store_dir=str(self.artifact_dir),
            replay_mode=replay_mode,
            is_fixture=False,
        )
        self.runtime.run_until_idle()
        return result


@pytest.fixture
def pipeline(tmp_path: Path) -> _Pipeline:
    source_dir = tmp_path / "source"
    artifact_dir = tmp_path / "artifacts"
    source_dir.mkdir()
    # This suite exercises the retained legacy extraction machinery
    # (direct evidence→candidate writes, replay re-extraction, version
    # disable). Post-migration (ADR 0026 step 3) that path is off by
    # default and must be selected explicitly; the shared-path behavior
    # is covered by tests/test_shared_extraction_migration.py.
    settings = ActivityNormalizerSettings(
        artifact_store_dir=str(artifact_dir),
        legacy_extraction_enabled=True,
    )

    runtime = Runtime(Graph())
    runtime.load_pack(core_pack, settings=CoreSettings())
    runtime.load_pack(activity_normalizer_pack, settings=settings)
    runtime.load_pack(
        local_files_pack,
        settings=LocalFilesSettings(artifact_store_dir=str(artifact_dir)),
    )
    return _Pipeline(runtime, source_dir, artifact_dir, settings)


def _write_source(root: Path, relative_path: str, text: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (_FIXED_FILE_TIME, _FIXED_FILE_TIME))
    return path


def _objects(graph, object_type: str):
    return list(graph.objects(type=object_type))


def _evidence(graph):
    return _objects(graph, "activity_evidence")


def _candidates(graph):
    return [
        candidate
        for candidate_type in _CANDIDATE_TYPES
        for candidate in graph.objects(type=candidate_type)
    ]


def _downstream_identity_snapshot(graph) -> dict[str, set[str]]:
    candidates = _candidates(graph)
    candidate_identities: set[str] = set()
    for candidate in candidates:
        candidate_identities.add(
            candidate.data.get("candidate_identity")
            or f"legacy-core:{candidate.id}"
        )
    return {
        "evidence": {obj.id for obj in _evidence(graph)},
        "extractions": {
            obj.id for obj in graph.objects(type="extraction_record")
        },
        "candidate_objects": {candidate.id for candidate in candidates},
        "candidate_identities": candidate_identities,
        "ingested_events": {
            event.id for event in graph.events if event.type == "source.event_ingested"
        },
    }


def test_repeat_import_has_no_new_evidence_or_downstream_duplicates(
    pipeline: _Pipeline,
) -> None:
    _write_source(
        pipeline.source_dir,
        "notes/profile.md",
        "Remember: My name is Yohei.\nPreference: concise status updates.\n",
    )

    first = pipeline.import_snapshot()
    assert first["ok"] is True
    assert first["imported"] == 1
    before = _downstream_identity_snapshot(pipeline.graph)
    assert len(before["evidence"]) == 1
    assert before["extractions"]
    assert before["candidate_objects"]
    assert len(before["ingested_events"]) == 1

    second = pipeline.import_snapshot()
    assert second["ok"] is True
    assert second["imported"] == 1  # duplicate delivery reaches the normalizer
    assert _downstream_identity_snapshot(pipeline.graph) == before


def test_overlapping_snapshot_reconciles_by_normalizer_identity(
    pipeline: _Pipeline,
) -> None:
    _write_source(pipeline.source_dir, "a.md", "Remember: alpha is stable.\n")
    assert pipeline.import_snapshot()["imported"] == 1
    first_identity = _evidence(pipeline.graph)[0].data["evidence_identity"]

    _write_source(pipeline.source_dir, "b.md", "Remember: beta is new.\n")
    overlap = pipeline.import_snapshot()
    assert overlap["imported"] == 2

    evidence = _evidence(pipeline.graph)
    assert len(evidence) == 2
    identities = [item.data["evidence_identity"] for item in evidence]
    assert len(set(identities)) == 2
    assert identities.count(first_identity) == 1
    snapshot = _downstream_identity_snapshot(pipeline.graph)
    assert len(snapshot["extractions"]) == 2
    assert len(snapshot["candidate_objects"]) == len(_candidates(pipeline.graph))
    assert len(snapshot["candidate_identities"]) == len(_candidates(pipeline.graph))


def test_edited_item_creates_linked_superseding_revision(
    pipeline: _Pipeline,
) -> None:
    source = _write_source(
        pipeline.source_dir,
        "changing.md",
        "Remember: deploy on Tuesday.\n",
    )
    assert pipeline.import_snapshot()["ok"] is True
    original = _evidence(pipeline.graph)[0]

    source.write_text("Remember: deploy on Wednesday.\n", encoding="utf-8")
    os.utime(source, (_FIXED_FILE_TIME, _FIXED_FILE_TIME))
    assert pipeline.import_snapshot()["ok"] is True

    evidence = sorted(_evidence(pipeline.graph), key=lambda item: item.data["revision_number"])
    assert len(evidence) == 2
    old, new = evidence
    assert old.id == original.id
    assert old.data["evidence_identity"] == new.data["evidence_identity"]
    assert [old.data["revision_number"], new.data["revision_number"]] == [1, 2]
    assert old.data["status"] == "superseded"
    assert new.data["status"] == "current"
    assert new.data["supersedes_evidence_id"] == old.id
    assert old.data["content_hash"] != new.data["content_hash"]
    assert any(
        relation.type == "supersedes"
        and relation.source == new.id
        and relation.target == old.id
        for relation in pipeline.graph.relations()
    )


def test_reextract_after_deleting_source_uses_recorded_artifact(
    pipeline: _Pipeline,
) -> None:
    _write_source(
        pipeline.source_dir,
        "replay.md",
        "Remember: replay must not touch the original file.\n",
    )
    assert pipeline.import_snapshot(replay_mode="artifact")["ok"] is True
    evidence = _evidence(pipeline.graph)[0]
    assert evidence.data["replay_complete"] is True
    assert evidence.data["replay_payload_ref"].startswith("artifact://sha256/")

    shutil.rmtree(pipeline.source_dir)
    assert not Path(evidence.data["source_ref"]).exists()
    result = reextract_evidence_fn(
        pipeline.graph,
        evidence.id,
        settings=pipeline.normalizer_settings,
        extractor_id="activity.structure",
        extractor_version="0.1.0",
        extraction_config_id="after-source-deletion",
    )

    assert result["ok"] is True
    record = pipeline.graph.get_object(result["extraction_record_id"])
    assert record.data["replayed"] is True
    assert record.data["replay_verified"] is True
    assert record.data["input_replay_payload_hash"] == evidence.data["replay_payload_hash"]
    assert any(event.type == "replay.verified" for event in pipeline.graph.events)


def test_reference_only_records_incomplete_and_replay_fails_loudly(
    pipeline: _Pipeline,
) -> None:
    _write_source(
        pipeline.source_dir,
        "private.md",
        "Remember: retained derived content has no replay payload.\n",
    )
    assert pipeline.import_snapshot(replay_mode="reference_only")["ok"] is True
    evidence = _evidence(pipeline.graph)[0]
    assert evidence.data["replay_mode"] == "reference_only"
    assert evidence.data["replay_complete"] is False

    shutil.rmtree(pipeline.source_dir)
    result = reextract_evidence_fn(
        pipeline.graph,
        evidence.id,
        settings=pipeline.normalizer_settings,
        extraction_config_id="must-fail-without-payload",
    )

    assert result["ok"] is False
    failure = pipeline.graph.get_object(result["failure_id"])
    assert failure.type == "ingestion_failure"
    assert failure.data["stage"] == "replay"
    assert "reference_only" in failure.data["message"]
    assert not any(
        record.data["extraction_config_id"] == "must-fail-without-payload"
        and record.data["status"] == "completed"
        for record in pipeline.graph.objects(type="extraction_record")
    )


def test_lost_replay_artifact_marks_evidence_incomplete(
    pipeline: _Pipeline,
) -> None:
    _write_source(
        pipeline.source_dir,
        "lost-artifact.md",
        "Remember: artifact loss must invalidate replay completeness.\n",
    )
    assert pipeline.import_snapshot(replay_mode="artifact")["ok"] is True
    evidence = _evidence(pipeline.graph)[0]
    retained = artifact_path(
        pipeline.artifact_dir,
        evidence.data["replay_payload_ref"],
    )
    retained.unlink()

    result = reextract_evidence_fn(
        pipeline.graph,
        evidence.id,
        settings=pipeline.normalizer_settings,
        extraction_config_id="missing-artifact",
    )

    assert result["ok"] is False
    assert pipeline.graph.get_object(evidence.id).data["replay_complete"] is False
    failure = pipeline.graph.get_object(result["failure_id"])
    assert failure.data["stage"] == "replay"
    assert failure.data["error_code"] == "replay_unavailable"


def test_malformed_input_records_failure_and_no_partial_evidence(
    pipeline: _Pipeline,
) -> None:
    _write_source(pipeline.source_dir, "broken.json", '{"unfinished":')

    result = pipeline.import_snapshot()

    assert result["ok"] is False
    assert result["imported"] == 0
    assert result["failed"] == 1
    assert len(result["failure_ids"]) == 1
    assert not _objects(pipeline.graph, "acquired_item")
    assert not _objects(pipeline.graph, "acquired_content")
    assert not _evidence(pipeline.graph)
    failure = pipeline.graph.get_object(result["failure_ids"][0])
    assert failure.type == "ingestion_failure"
    assert failure.data["stage"] == "acquisition"
    assert failure.data["error_code"] == "invalid_json"


def test_every_candidate_has_full_evidence_and_version_provenance(
    pipeline: _Pipeline,
) -> None:
    _write_source(
        pipeline.source_dir,
        "provenance.md",
        "\n".join(
            [
                "Remember: The project codename is ActiveGraph.",
                "Preference: I prefer concise written updates.",
                "TODO: document the deterministic replay contract.",
                "Profile: I maintain the local knowledge source.",
                "Skill: summarize a source with citations.",
                "Evaluation: the importer result was helpful.",
            ]
        ),
    )
    assert pipeline.import_snapshot()["ok"] is True

    candidates = _candidates(pipeline.graph)
    assert candidates
    relations = list(pipeline.graph.relations())
    for candidate in candidates:
        evidence_links = [
            relation
            for relation in relations
            if relation.type == "extracted_from" and relation.source == candidate.id
        ]
        assert len(evidence_links) == 1, candidate.id
        evidence = pipeline.graph.get_object(evidence_links[0].target)
        assert evidence.type == "activity_evidence"
        assert evidence.data["importer_id"] == "local_files"
        assert evidence.data["importer_version"] == "0.1.0"
        assert evidence.data["source_ref"]
        assert evidence.data["source_surface_id"] == pipeline.surface_id

        extraction_links = [
            relation
            for relation in relations
            if relation.type == "produced_candidate" and relation.target == candidate.id
        ]
        assert len(extraction_links) == 1, candidate.id
        extraction = pipeline.graph.get_object(extraction_links[0].source)
        assert extraction.type == "extraction_record"
        assert extraction.data["evidence_id"] == evidence.id
        assert extraction.data["evidence_identity"] == evidence.data["evidence_identity"]
        assert extraction.data["revision_id"] == evidence.data["revision_id"]
        assert extraction.data["extractor_id"]
        assert extraction.data["extractor_version"]
        assert extraction.data["extraction_config_id"]
        assert candidate.id in extraction.data["candidate_ids"]

        if candidate.type == "memory_candidate":
            assert evidence.id in candidate.data["source_ids"]
        else:
            assert candidate.data["evidence_id"] == evidence.id
            assert candidate.data["evidence_identity"] == evidence.data["evidence_identity"]
            assert candidate.data["revision_id"] == evidence.data["revision_id"]
            assert candidate.data["extractor_id"] == extraction.data["extractor_id"]
            assert candidate.data["extractor_version"] == extraction.data["extractor_version"]
