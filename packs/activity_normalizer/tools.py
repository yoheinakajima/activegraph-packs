"""Graph-visible replay and extractor-lifecycle capabilities."""

from __future__ import annotations

from typing import Optional

from activegraph.packs import tool

from .behaviors import (
    emit_domain_event,
    normalize_replay_payload,
    record_failure,
    run_extraction,
)
from .replay import ReplayError, read_replay_payload
from .settings import ActivityNormalizerSettings


def reextract_evidence_fn(
    graph,
    evidence_id: str,
    *,
    settings: ActivityNormalizerSettings,
    extractor_id: Optional[str] = None,
    extractor_version: Optional[str] = None,
    extraction_config_id: Optional[str] = None,
) -> dict:
    """Re-extract only from retained payload; never dereference source_ref."""

    evidence = graph.get_object(evidence_id)
    if evidence is None or evidence.type != "activity_evidence":
        raise ValueError(f"unknown activity_evidence: {evidence_id}")
    data = evidence.data
    chosen_id = extractor_id or settings.default_extractor_id
    chosen_version = extractor_version or settings.default_extractor_version
    chosen_config = extraction_config_id or settings.default_extraction_config_id
    try:
        payload = read_replay_payload(data, settings)
        content, replay_metadata = normalize_replay_payload(
            payload,
            data["media_type"],
            encoding=data.get("encoding") or settings.encoding,
            max_chars=settings.max_normalized_content_chars,
        )
        metadata = dict(data.get("normalized_metadata") or {})
        metadata.update(replay_metadata)
        result = run_extraction(
            graph,
            evidence,
            content,
            metadata,
            settings=settings,
            extractor_id=chosen_id,
            extractor_version=chosen_version,
            extraction_config_id=chosen_config,
            replayed=True,
            replay_verified=True,
        )
    except (ReplayError, UnicodeError, ValueError, KeyError, RuntimeError) as exc:
        if isinstance(exc, ReplayError) and data.get("replay_complete", False):
            graph.patch_object(
                evidence.id,
                {"replay_complete": False},
                rationale="retained replay payload is unavailable or failed verification",
            )
        failure = record_failure(
            graph,
            stage="replay" if isinstance(exc, ReplayError) else "extraction",
            error_code=(
                "replay_unavailable" if isinstance(exc, ReplayError) else "reextraction_failed"
            ),
            message=f"{type(exc).__name__}: {exc}",
            source_surface_id=data.get("source_surface_id"),
            acquired_item_id=data.get("acquired_item_id"),
            source_ref=data.get("source_ref"),
            importer_id=data.get("importer_id"),
            importer_version=data.get("importer_version"),
            extractor_id=chosen_id,
            extractor_version=chosen_version,
        )
        return {"ok": False, "failure_id": failure.id}

    record = result["record"]
    event = emit_domain_event(
        graph,
        "replay.verified",
        {
            "evidence_id": evidence.id,
            "evidence_identity": data["evidence_identity"],
            "revision_id": data["revision_id"],
            "extraction_record_id": record.id,
            "extractor_id": chosen_id,
            "extractor_version": chosen_version,
            "extraction_config_id": chosen_config,
            "replay_payload_hash": data["replay_payload_hash"],
        },
    )
    return {
        "ok": True,
        "extraction_record_id": record.id,
        "replay_event_id": event.id,
        "created": result["created"],
    }


def disable_extractor_version_fn(
    graph,
    extractor_id: str,
    extractor_version: str,
    *,
    reason: str,
    extraction_config_id: Optional[str] = None,
) -> dict:
    """Invalidate an extractor version's candidate provenance, preserving evidence."""

    state_identity = (
        f"{extractor_id}@{extractor_version}"
        + (f"#{extraction_config_id}" if extraction_config_id else "")
    )
    event = emit_domain_event(
        graph,
        "extractor.disabled",
        {
            "state_identity": state_identity,
            "extractor_id": extractor_id,
            "extractor_version": extractor_version,
            "extraction_config_id": extraction_config_id,
            "reason": reason,
        },
    )
    state = graph.add_object(
        "extractor_version_state",
        {
            "state_identity": state_identity,
            "extractor_id": extractor_id,
            "extractor_version": extractor_version,
            "extraction_config_id": extraction_config_id,
            "status": "disabled",
            "reason": reason,
            "changed_at_event_id": event.id,
        },
    )
    invalidated_records = 0
    invalidated_candidates = 0
    for record in graph.objects(type="extraction_record"):
        data = record.data
        if data.get("extractor_id") != extractor_id or data.get("extractor_version") != extractor_version:
            continue
        if extraction_config_id and data.get("extraction_config_id") != extraction_config_id:
            continue
        if data.get("status") != "invalidated":
            graph.patch_object(
                record.id,
                {"status": "invalidated"},
                rationale=reason,
            )
            invalidated_records += 1
        for candidate_id in data.get("candidate_ids") or []:
            candidate = graph.get_object(candidate_id)
            if candidate is None:
                continue
            if candidate.type == "memory_candidate":
                graph.add_object(
                    "evaluation",
                    {
                        "subject_id": candidate.id,
                        "subject_type": "memory_candidate",
                        "judgment": "invalidated",
                        "rationale": reason,
                        "evaluator": "activity_normalizer.disable_extractor_version",
                        "score": None,
                        "frame_id": None,
                        "metadata": {
                            "extractor_id": extractor_id,
                            "extractor_version": extractor_version,
                        },
                    },
                )
            else:
                graph.patch_object(
                    candidate.id,
                    {"status": "invalidated", "invalidation_reason": reason},
                    rationale=reason,
                )
            invalidated_candidates += 1
    return {
        "ok": True,
        "state_id": state.id,
        "event_id": event.id,
        "invalidated_records": invalidated_records,
        "invalidated_candidates": invalidated_candidates,
    }


@tool(
    name="reextract_evidence",
    description="Re-run a versioned extractor from retained replay input without source access.",
    deterministic=True,
)
def reextract_evidence(
    graph,
    evidence_id: str,
    artifact_store_dir: str = ".activegraph/replay-artifacts",
    extractor_id: str = "activity.structure",
    extractor_version: str = "0.1.0",
    extraction_config_id: str = "default",
) -> dict:
    return reextract_evidence_fn(
        graph,
        evidence_id,
        settings=ActivityNormalizerSettings(artifact_store_dir=artifact_store_dir),
        extractor_id=extractor_id,
        extractor_version=extractor_version,
        extraction_config_id=extraction_config_id,
    )


@tool(
    name="disable_extractor_version",
    description="Disable an extractor version and invalidate its candidate provenance.",
    deterministic=True,
)
def disable_extractor_version(
    graph,
    extractor_id: str,
    extractor_version: str = "0.1.0",
    reason: str = "disabled by explicit operator action",
    extraction_config_id: Optional[str] = None,
) -> dict:
    return disable_extractor_version_fn(
        graph,
        extractor_id,
        extractor_version,
        reason=reason,
        extraction_config_id=extraction_config_id,
    )


TOOLS = [reextract_evidence, disable_extractor_version]


__all__ = [
    "TOOLS",
    "reextract_evidence_fn",
    "disable_extractor_version_fn",
]
