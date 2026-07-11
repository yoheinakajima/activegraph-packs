"""Identity-owning normalization and deterministic extraction behaviors."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from activegraph import Event
from activegraph.packs import behavior

from .extractors import CandidateDraft, get_extractor
from .object_types import ActivityEvidence
from .replay import read_replay_payload
from .settings import ActivityNormalizerSettings


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()}"


def emit_domain_event(
    graph,
    event_type: str,
    payload: dict[str, Any],
    *,
    caused_by: str | None = None,
) -> Event:
    """Emit one immutable domain event using the graph's recorded clock."""

    # Behaviors receive the constrained BehaviorGraph, whose emit surface
    # accepts (type, payload) and stamps identity/time/causality. Raw tools
    # receive Graph and construct the domain Event explicitly.
    if not hasattr(graph, "ids"):
        return graph.emit(event_type, payload)
    event = Event(
        id=graph.ids.event(),
        type=event_type,
        payload=payload,
        actor="activity_normalizer",
        caused_by=caused_by,
        timestamp=graph.clock.now(),
    )
    graph.emit(event)
    return event


def _patch(graph, target: str, updates: dict[str, Any], *, rationale: str) -> None:
    if hasattr(graph, "objects"):
        graph.patch_object(target, updates, rationale=rationale)
    else:
        graph.patch_object(target, updates)


def record_failure(
    graph,
    *,
    stage: str,
    error_code: str,
    message: str,
    source_surface_id: str | None = None,
    acquired_item_id: str | None = None,
    source_ref: str | None = None,
    importer_id: str | None = None,
    importer_version: str | None = None,
    extractor_id: str | None = None,
    extractor_version: str | None = None,
    recoverable: bool = False,
    metadata: dict[str, Any] | None = None,
):
    failure = graph.add_object(
        "ingestion_failure",
        {
            "source_surface_id": source_surface_id,
            "acquired_item_id": acquired_item_id,
            "source_ref": source_ref,
            "stage": stage,
            "error_code": error_code,
            "message": message[:2000] or error_code,
            "importer_id": importer_id,
            "importer_version": importer_version,
            "extractor_id": extractor_id,
            "extractor_version": extractor_version,
            "recoverable": recoverable,
            "metadata": metadata or {},
        },
    )
    if acquired_item_id:
        target = graph.get_object(acquired_item_id)
        if target is not None:
            graph.add_relation(failure.id, acquired_item_id, "failure_for")
    return failure


def normalize_replay_payload(
    payload: bytes,
    media_type: str,
    *,
    encoding: str,
    max_chars: int,
) -> tuple[str, dict[str, Any]]:
    """Derive bounded reasoning text from the exact retained replay payload."""

    text = payload.decode(encoding)
    metadata: dict[str, Any] = {}
    if media_type == "application/json" or media_type.endswith("+json"):
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            for key in ("normalized_content", "text"):
                if isinstance(parsed.get(key), str):
                    metadata = {
                        str(k): v
                        for k, v in parsed.items()
                        if k not in {"normalized_content", "text"}
                        and isinstance(v, (str, int, float, bool, type(None), list, dict))
                    }
                    text = parsed[key]
                    break
            else:
                content = parsed.get("content")
                if isinstance(content, str):
                    text = content
                elif isinstance(content, dict) and isinstance(content.get("parts"), list):
                    text = "\n".join(str(part) for part in content["parts"] if part is not None)
                else:
                    text = json.dumps(parsed, sort_keys=True, ensure_ascii=False)
        else:
            text = json.dumps(parsed, sort_keys=True, ensure_ascii=False)
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return text[:max_chars], metadata


def _content_for(read_view, acquired_item_id: str):
    matches = [
        obj
        for obj in read_view.objects(type="acquired_content")
        if (obj.data or {}).get("acquired_item_id") == acquired_item_id
    ]
    return matches[-1] if matches else None


def _extractor_disabled(
    read_view,
    extractor_id: str,
    extractor_version: str,
    extraction_config_id: str,
) -> bool:
    states = [
        obj.data
        for obj in read_view.objects(type="extractor_version_state")
        if obj.data.get("extractor_id") == extractor_id
        and obj.data.get("extractor_version") == extractor_version
        and obj.data.get("extraction_config_id") in (None, extraction_config_id)
    ]
    return bool(states and states[-1].get("status") == "disabled")


def _candidate_identity(
    evidence: dict[str, Any],
    extractor_id: str,
    extractor_version: str,
    extraction_config_id: str,
    draft: CandidateDraft,
    ordinal: int,
) -> str:
    return _stable_id(
        "candidate",
        evidence["revision_id"],
        extractor_id,
        extractor_version,
        extraction_config_id,
        draft.kind,
        draft.text,
        ordinal,
    )


def _create_candidate(
    graph,
    evidence_obj,
    extraction_record_id: str,
    draft: CandidateDraft,
    candidate_identity: str,
    *,
    extractor_id: str,
    extractor_version: str,
    extraction_config_id: str,
):
    evidence = evidence_obj.data
    if draft.kind == "memory":
        candidate_type = "memory_candidate"
        data = {
            "text": draft.text,
            "confidence": draft.confidence,
            "source_ids": [evidence_obj.id],
            "observation_ids": [],
            "category": draft.fields.get("category", "context"),
            "subject_ref": None,
            "accepted": False,
            "evaluation_id": None,
            "frame_id": None,
        }
    else:
        candidate_type = {
            "preference": "preference_candidate",
            "task": "task_candidate",
            "profile": "profile_candidate",
            "skill": "skill_candidate",
            "eval": "eval_candidate",
        }[draft.kind]
        data = {
            "candidate_identity": candidate_identity,
            "text": draft.text,
            "confidence": draft.confidence,
            "evidence_id": evidence_obj.id,
            "evidence_identity": evidence["evidence_identity"],
            "revision_id": evidence["revision_id"],
            "extraction_record_id": extraction_record_id,
            "extractor_id": extractor_id,
            "extractor_version": extractor_version,
            "extraction_config_id": extraction_config_id,
            "status": "candidate",
            "invalidation_reason": None,
            "metadata": {},
            **draft.fields,
        }
    candidate = graph.add_object(candidate_type, data)
    graph.add_relation(extraction_record_id, candidate.id, "produced_candidate")
    graph.add_relation(candidate.id, evidence_obj.id, "extracted_from")
    return candidate


def run_extraction(
    graph,
    evidence_obj,
    content: str,
    normalized_metadata: dict[str, Any],
    *,
    settings: ActivityNormalizerSettings,
    extractor_id: str,
    extractor_version: str,
    extraction_config_id: str,
    replayed: bool = False,
    replay_verified: bool = False,
    read_view=None,
) -> dict[str, Any]:
    """Run one immutable extractor/config identity idempotently."""

    evidence = evidence_obj.data
    extraction_identity = _stable_id(
        "extraction",
        evidence["revision_id"],
        extractor_id,
        extractor_version,
        extraction_config_id,
    )
    reader = read_view or graph
    existing = next(
        (
            obj
            for obj in reader.objects(type="extraction_record")
            if obj.data.get("extraction_identity") == extraction_identity
        ),
        None,
    )
    if existing is not None:
        return {"ok": existing.data.get("status") == "completed", "record": existing, "created": False}
    if _extractor_disabled(reader, extractor_id, extractor_version, extraction_config_id):
        raise RuntimeError(f"extractor is disabled: {extractor_id}@{extractor_version}")

    extractor = get_extractor(extractor_id, extractor_version)
    drafts = extractor.extract(
        content,
        normalized_metadata,
        max_candidates=settings.max_candidates_per_evidence,
        max_candidate_chars=settings.max_candidate_chars,
    )
    record = graph.add_object(
        "extraction_record",
        {
            "extraction_identity": extraction_identity,
            "evidence_id": evidence_obj.id,
            "evidence_identity": evidence["evidence_identity"],
            "revision_id": evidence["revision_id"],
            "extractor_id": extractor_id,
            "extractor_version": extractor_version,
            "extraction_config_id": extraction_config_id,
            "input_content_hash": evidence["content_hash"],
            "input_replay_payload_hash": evidence["replay_payload_hash"],
            "candidate_ids": [],
            "candidate_types": [],
            "status": "completed",
            "replayed": replayed,
            "replay_verified": replay_verified,
            "error": None,
            "metadata": {},
        },
    )
    graph.add_relation(record.id, evidence_obj.id, "extraction_for")

    candidate_ids: list[str] = []
    candidate_types: list[str] = []
    for ordinal, draft in enumerate(drafts):
        identity = _candidate_identity(
            evidence,
            extractor_id,
            extractor_version,
            extraction_config_id,
            draft,
            ordinal,
        )
        candidate = _create_candidate(
            graph,
            evidence_obj,
            record.id,
            draft,
            identity,
            extractor_id=extractor_id,
            extractor_version=extractor_version,
            extraction_config_id=extraction_config_id,
        )
        candidate_ids.append(candidate.id)
        candidate_types.append(candidate.type)
    _patch(
        graph,
        record.id,
        {"candidate_ids": candidate_ids, "candidate_types": candidate_types},
        rationale="record deterministic extractor outputs",
    )
    return {"ok": True, "record": graph.get_object(record.id), "created": True}


@behavior(
    name="normalize_acquired_item",
    on=["object.created"],
    where={"object.type": "acquired_item"},
    view={
        "include_types": [
            "acquired_content",
            "activity_evidence",
            "extraction_record",
            "extractor_version_state",
        ]
    },
    creates=[
        "activity_evidence",
        "extraction_record",
        "memory_candidate",
        "preference_candidate",
        "task_candidate",
        "profile_candidate",
        "skill_candidate",
        "eval_candidate",
        "ingestion_failure",
    ],
)
def normalize_acquired_item(event, graph, ctx, *, settings: ActivityNormalizerSettings):
    """Validate one acquisition, own identity/revision, then extract candidates."""

    if not settings.enabled:
        return
    wrapper = event.payload.get("object", {})
    acquired_item_id = wrapper.get("id")
    item = wrapper.get("data", {})
    content_obj = _content_for(ctx.view, acquired_item_id)
    if content_obj is None:
        record_failure(
            graph,
            stage="normalization",
            error_code="missing_acquired_content",
            message="acquired_item has no paired acquired_content handoff",
            source_surface_id=item.get("source_surface_id"),
            acquired_item_id=acquired_item_id,
            source_ref=item.get("source_ref"),
            importer_id=item.get("importer_id"),
            importer_version=item.get("importer_version"),
        )
        return

    handoff = content_obj.data
    try:
        if item["replay_mode"] != "reference_only":
            read_replay_payload(item, settings)
        normalized_content = str(handoff.get("normalized_content", ""))[
            : settings.max_normalized_content_chars
        ]
        content_hash = item.get("source_hash") or item["replay_payload_hash"]
        identity = _stable_id("evidence", item["source_surface_id"], item["dedup_key"])
        revisions = [
            obj
            for obj in ctx.view.objects(type="activity_evidence")
            if obj.data.get("evidence_identity") == identity
        ]
        revisions.sort(key=lambda obj: obj.data.get("revision_number", 0))
        current = revisions[-1] if revisions else None
        if current is not None and current.data.get("content_hash") == content_hash:
            return

        revision_number = (current.data["revision_number"] + 1) if current else 1
        revision_id = _stable_id("revision", identity, revision_number, content_hash)
        evidence_data = ActivityEvidence(
            evidence_identity=identity,
            revision_id=revision_id,
            revision_number=revision_number,
            status="current",
            acquired_item_id=acquired_item_id,
            acquired_content_id=content_obj.id,
            source_surface_id=item["source_surface_id"],
            provider_item_id=item.get("provider_item_id"),
            dedup_key=item["dedup_key"],
            source_ref=item["source_ref"],
            source_hash=item.get("source_hash"),
            content_hash=content_hash,
            provider_time=item.get("provider_time"),
            replay_mode=item["replay_mode"],
            replay_payload_ref=item["replay_payload_ref"],
            replay_payload_hash=item["replay_payload_hash"],
            replay_complete=item["replay_mode"] != "reference_only",
            media_type=item["media_type"],
            encoding=settings.encoding,
            retention_policy=settings.retention_policy,
            acquired_at_event_id=event.id,
            normalized_content=normalized_content,
            normalized_metadata=handoff.get("normalized_metadata") or {},
            source_category=handoff["source_category"],
            connection_path=handoff["connection_path"],
            importer_id=item["importer_id"],
            importer_version=item["importer_version"],
            is_fixture=bool(handoff["is_fixture"]),
            supersedes_evidence_id=current.id if current else None,
        ).model_dump()

        if current is not None:
            _patch(
                graph,
                current.id,
                {"status": "superseded"},
                rationale=f"superseded by evidence revision {revision_id}",
            )
        evidence_obj = graph.add_object("activity_evidence", evidence_data)
        graph.add_relation(content_obj.id, acquired_item_id, "content_for")
        graph.add_relation(content_obj.id, evidence_obj.id, "normalizes_to")
        graph.add_relation(evidence_obj.id, acquired_item_id, "acquired_from")
        if current is not None:
            graph.add_relation(evidence_obj.id, current.id, "supersedes")

        # ADR 0026 step 3: the direct evidence→candidate write path is
        # disabled. Extraction runs on the shared annotation layer (the
        # semantic_extraction pack annotates this evidence eagerly) and
        # the compatibility projectors below mint the same candidates
        # from annotations. The legacy path stays available only as an
        # explicit opt-in for rollback.
        if settings.legacy_extraction_enabled:
            try:
                run_extraction(
                    graph,
                    evidence_obj,
                    normalized_content,
                    evidence_data["normalized_metadata"],
                    settings=settings,
                    extractor_id=settings.default_extractor_id,
                    extractor_version=settings.default_extractor_version,
                    extraction_config_id=settings.default_extraction_config_id,
                    read_view=ctx.view,
                )
            except Exception as exc:
                record_failure(
                    graph,
                    stage="extraction",
                    error_code="extractor_failed",
                    message=f"{type(exc).__name__}: {exc}",
                    source_surface_id=item["source_surface_id"],
                    acquired_item_id=acquired_item_id,
                    source_ref=item["source_ref"],
                    importer_id=item["importer_id"],
                    importer_version=item["importer_version"],
                    extractor_id=settings.default_extractor_id,
                    extractor_version=settings.default_extractor_version,
                )

        if settings.emit_custom_events:
            emit_domain_event(
                graph,
                "source.event_ingested",
                {
                    "evidence_identity": identity,
                    "evidence_id": evidence_obj.id,
                    "revision_id": revision_id,
                    "revision_number": revision_number,
                    "source_surface_id": item["source_surface_id"],
                    "provider_item_id": item.get("provider_item_id"),
                    "source_ref": item["source_ref"],
                    "content_hash": content_hash,
                    "provider_time": item.get("provider_time"),
                    "source_category": handoff["source_category"],
                    "connection_path": handoff["connection_path"],
                    "importer_id": item["importer_id"],
                    "importer_version": item["importer_version"],
                    "is_fixture": bool(handoff["is_fixture"]),
                    "replay_complete": item["replay_mode"] != "reference_only",
                    "invalidated": False,
                },
                caused_by=event.id,
            )
    except Exception as exc:
        record_failure(
            graph,
            stage="normalization",
            error_code="normalization_failed",
            message=f"{type(exc).__name__}: {exc}",
            source_surface_id=item.get("source_surface_id"),
            acquired_item_id=acquired_item_id,
            source_ref=item.get("source_ref"),
            importer_id=item.get("importer_id"),
            importer_version=item.get("importer_version"),
        )


def _emit_cursor_event(graph, cursor, caused_by: str) -> None:
    emit_domain_event(
        graph,
        "source.cursor_advanced",
        {
            "cursor_id": cursor.id,
            "source_surface_id": cursor.data["source_surface_id"],
            "oldest_ingested_ref": cursor.data.get("oldest_ingested_ref"),
            "newest_ingested_ref": cursor.data.get("newest_ingested_ref"),
            "watermark_ref": cursor.data.get("watermark_ref"),
            "cursor_version": cursor.data["cursor_version"],
        },
        caused_by=caused_by,
    )


@behavior(
    name="publish_cursor_created",
    on=["object.created"],
    where={"object.type": "backfill_cursor"},
    creates=[],
)
def publish_cursor_created(event, graph, ctx, *, settings: ActivityNormalizerSettings):
    """Publish provider-stable cursor state for observational consumers."""

    if settings.emit_custom_events:
        cursor = graph.get_object(event.payload.get("object", {}).get("id"))
        if cursor is not None:
            _emit_cursor_event(graph, cursor, event.id)


@behavior(name="publish_cursor_patch", on=["patch.applied"], creates=[])
def publish_cursor_patch(event, graph, ctx, *, settings: ActivityNormalizerSettings):
    """Publish each committed cursor advance without interpreting pagination."""

    if not settings.emit_custom_events:
        return
    target = event.payload.get("target")
    cursor = graph.get_object(target) if target else None
    if cursor is not None and cursor.type == "backfill_cursor":
        _emit_cursor_event(graph, cursor, event.id)


@behavior(
    name="fulfill_evidence_invalidation_request",
    on=["object.created"],
    where={"object.type": "evidence_invalidation_request"},
    view={"include_types": ["activity_evidence", "evidence_invalidation_request"]},
    creates=[],
)
def fulfill_evidence_invalidation_request(
    event, graph, ctx, *, settings: ActivityNormalizerSettings
):
    """Turn one provider tombstone into explicit, reversible invalidation."""

    wrapper = event.payload.get("object") or {}
    request_id = wrapper.get("id")
    data = wrapper.get("data") or {}
    if not request_id or data.get("status") != "proposed":
        return
    matches = [
        obj
        for obj in ctx.view.objects(type="activity_evidence")
        if obj.data.get("source_surface_id") == data.get("source_surface_id")
        and obj.data.get("status") == "current"
        and (
            (
                data.get("evidence_identity")
                and obj.data.get("evidence_identity") == data.get("evidence_identity")
            )
            or (
                data.get("provider_item_id")
                and obj.data.get("provider_item_id") == data.get("provider_item_id")
            )
        )
    ]
    invalidated = []
    for evidence in matches:
        graph.patch_object(evidence.id, {"status": "revoked"})
        emit_domain_event(
            graph,
            "source.evidence_invalidated",
            {
                "request_id": request_id,
                "evidence_id": evidence.id,
                "evidence_identity": evidence.data["evidence_identity"],
                "source_surface_id": evidence.data["source_surface_id"],
                "provider_item_id": evidence.data.get("provider_item_id"),
                "reason": data.get("reason"),
            },
            caused_by=event.id,
        )
        invalidated.append(evidence.id)
    # A tombstone for an item outside the retained window is an inspectable
    # successful no-op, not an error and not a fabricated evidence object.
    graph.patch_object(
        request_id,
        {"status": "fulfilled", "invalidated_evidence_ids": invalidated},
    )


# ------------------------------------------------- ADR 0026 steps 2-3:
# the shared-extraction selection + the compatibility candidate projectors.

from .annotation_extractor import (  # noqa: E402  (registers the extractor)
    KIND_BY_FACET,
    STRUCTURE_FACETS,
)

STRUCTURE_EXTRACTOR_REF = "activity.structure@0.2.0"


@behavior(
    name="select_shared_extraction",
    on=["object.created"],
    where={"object.type": "extraction_profile"},
    view={"include_types": ["extraction_profile"]},
    creates=["extraction_profile"],
)
def select_shared_extraction(event, graph, ctx, *, settings: ActivityNormalizerSettings):
    """Route the activity.* structure facets onto the shared layer the
    moment its default profile appears (ADR 0026: curated selection of
    the shared path — no long legacy window, D041).

    Reacts only to the seeded v1 profile; later versions are owner
    policy. Idempotent across boots: replayed stores already carry v2,
    and pack.loaded re-seeding never re-creates v1.
    """
    if not (settings.enabled and settings.select_shared_extraction):
        return
    if settings.legacy_extraction_enabled:
        return
    wrapper = event.payload.get("object", {})
    data = wrapper.get("data", {})
    if data.get("version") != 1 or data.get("status") != "active":
        return
    routed = data.get("extractor_by_facet") or {}
    if any(facet in routed for facet in STRUCTURE_FACETS):
        return
    if any(
        obj.data.get("version", 0) > 1
        for obj in ctx.view.objects(type="extraction_profile")
    ):
        return
    extractor_by_facet = dict(routed)
    extractor_by_facet.update(
        {facet: STRUCTURE_EXTRACTOR_REF for facet in STRUCTURE_FACETS}
    )
    graph.add_object(
        "extraction_profile",
        {
            "profile_identity": _stable_id("extraction_profile", 2),
            "version": 2,
            "status": "active",
            "default_facets": sorted(
                {*(data.get("default_facets") or []), *STRUCTURE_FACETS}
            ),
            "facets_by_source_category": dict(
                data.get("facets_by_source_category") or {}
            ),
            "extractor_by_facet": extractor_by_facet,
            "created_by": "activity_normalizer.select_shared_extraction",
            "rationale": (
                "ADR 0026 steps 2-3: activity structure extraction moves "
                "onto the shared annotation layer; the direct "
                "evidence→candidate write path is disabled"
            ),
            "supersedes_profile_id": wrapper.get("id"),
        },
    )
    _patch(
        graph,
        wrapper.get("id"),
        {"status": "superseded"},
        rationale="superseded by extraction_profile v2 (shared-path selection)",
    )


_COMPAT_VIEW = {
    "include_types": [
        "memory_candidate",
        "preference_candidate",
        "task_candidate",
        "profile_candidate",
        "skill_candidate",
        "eval_candidate",
    ]
}


@behavior(
    name="project_structure_candidates",
    on=["object.created"],
    where={"object.type": "semantic_annotation"},
    view=_COMPAT_VIEW,
    creates=[
        "memory_candidate",
        "preference_candidate",
        "task_candidate",
        "profile_candidate",
        "skill_candidate",
        "eval_candidate",
    ],
)
def project_structure_candidates(
    event, graph, ctx, *, settings: ActivityNormalizerSettings
):
    """Compatibility candidate projectors (ADR 0026 step 2).

    One activity.* annotation → the same candidate object the legacy
    direct write path produced, deduped by the LEGACY candidate identity
    (revision, activity.structure@0.1.0, config, kind, text, ordinal) so
    re-running extraction over evidence a pre-migration graph already
    extracted creates nothing new.
    """
    if not (settings.enabled and settings.compat_candidate_projectors):
        return
    wrapper = event.payload.get("object", {})
    data = wrapper.get("data", {})
    facet = data.get("facet", "")
    kind = KIND_BY_FACET.get(facet)
    if kind is None or data.get("status") != "active":
        return
    body = data.get("body") or {}
    text = body.get("text", "")
    if not text:
        return
    annotation_id = wrapper.get("id")
    evidence_id = data.get("evidence_id")
    metadata = data.get("metadata") or {}
    ordinal = metadata.get("ordinal", 0)

    if kind == "memory":
        # Legacy memory candidates carry no identity field; dedup by
        # (text, evidence source) exactly as a re-run would collide.
        for existing in ctx.view.objects(type="memory_candidate"):
            existing_data = existing.data
            if existing_data.get("text") == text and evidence_id in (
                existing_data.get("source_ids") or []
            ):
                return
        candidate = graph.add_object(
            "memory_candidate",
            {
                "text": text,
                "confidence": data.get("confidence", 0.7),
                "source_ids": [evidence_id],
                "observation_ids": [annotation_id],
                "category": body.get("category", "context"),
                "subject_ref": None,
                "accepted": False,
                "evaluation_id": None,
                "frame_id": None,
            },
        )
        graph.add_relation(candidate.id, annotation_id, "projected_from_annotation")
        graph.add_relation(candidate.id, evidence_id, "extracted_from")
        return

    candidate_type = {
        "preference": "preference_candidate",
        "task": "task_candidate",
        "profile": "profile_candidate",
        "skill": "skill_candidate",
        "eval": "eval_candidate",
    }[kind]
    legacy_identity = _stable_id(
        "candidate",
        data.get("revision_id"),
        settings.default_extractor_id,
        settings.default_extractor_version,
        settings.default_extraction_config_id,
        kind,
        text,
        ordinal,
    )
    for existing in ctx.view.objects(type=candidate_type):
        if existing.data.get("candidate_identity") == legacy_identity:
            return

    fields = {
        key: value
        for key, value in body.items()
        if key not in ("text", "kind")
    }
    candidate = graph.add_object(
        candidate_type,
        {
            "candidate_identity": legacy_identity,
            "text": text,
            "confidence": data.get("confidence", 0.7),
            "evidence_id": evidence_id,
            "evidence_identity": data.get("evidence_identity"),
            "revision_id": data.get("revision_id"),
            "extraction_record_id": data.get("run_id"),
            "extractor_id": data.get("extractor_id"),
            "extractor_version": data.get("extractor_version"),
            "extraction_config_id": data.get("config_hash"),
            "status": "candidate",
            "invalidation_reason": None,
            "metadata": {
                "projector": "activity_normalizer.compat",
                "annotation_id": annotation_id,
                "annotation_identity": data.get("annotation_identity"),
            },
            **fields,
        },
    )
    graph.add_relation(candidate.id, annotation_id, "projected_from_annotation")
    graph.add_relation(candidate.id, evidence_id, "extracted_from")


BEHAVIORS = [
    normalize_acquired_item,
    fulfill_evidence_invalidation_request,
    publish_cursor_created,
    publish_cursor_patch,
    select_shared_extraction,
    project_structure_candidates,
]


__all__ = [
    "BEHAVIORS",
    "emit_domain_event",
    "normalize_replay_payload",
    "record_failure",
    "run_extraction",
]
