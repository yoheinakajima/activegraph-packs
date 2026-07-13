"""Versioned connector budgets and a non-persisted conformance measurer."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from packs.activity_normalizer.replay import artifact_path

from .object_types import ConnectorOperationalPolicy


OPERATIONAL_POLICY_ID = "connector-operational@0.2.0"
SUPERSEDED_POLICY_IDS = ("connector-operational@0.1.0",)


def operational_policy_payload() -> dict[str, Any]:
    return ConnectorOperationalPolicy(
        policy_identity=OPERATIONAL_POLICY_ID,
        version=2,
        rationale=(
            "ADR 0034 provisional local release bounds for the recorded "
            "250-item deterministic connector fixture; v2 names the "
            "acquisition ceilings ingestion plans validate against (ADR 0039)"
        ),
    ).model_dump()


class ConnectorOperationalMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_identity: str = OPERATIONAL_POLICY_ID
    domain_run_id: str
    evidence_items: int = Field(ge=0)
    durable_events: int = Field(ge=0)
    events_per_evidence: float = Field(ge=0)
    annotations: int = Field(ge=0)
    max_annotations_for_one_evidence: int = Field(ge=0)
    behavior_firings: int = Field(ge=0)
    behavior_firings_per_evidence: float = Field(ge=0)
    provider_calls: int = Field(ge=0)
    artifact_bytes: int = Field(ge=0)
    max_queue_depth: Optional[int] = Field(default=None, ge=0)
    acknowledgement_ms: Optional[float] = Field(default=None, ge=0)
    first_progress_ms: Optional[float] = Field(default=None, ge=0)
    projection_read_p95_ms: Optional[float] = Field(default=None, ge=0)
    max_unyielded_ms: Optional[float] = Field(default=None, ge=0)


def _run_evidence(graph, run_id: str):
    return [
        obj for obj in graph.objects(type="activity_evidence")
        if (obj.data.get("normalized_metadata") or {}).get("connector_run_id")
        == run_id
    ]


def _event_start(graph, run_id: str) -> int:
    for index, event in enumerate(graph.events):
        if event.type != "object.created":
            continue
        wrapper = (event.payload or {}).get("object") or {}
        if wrapper.get("id") == run_id:
            return index
    raise ValueError(f"domain run {run_id!r} has no object.created event")


def measure_connector_run(
    graph,
    domain_run_id: str,
    *,
    artifact_store_dir: str | Path,
    max_queue_depth: Optional[int] = None,
    acknowledgement_ms: Optional[float] = None,
    first_progress_ms: Optional[float] = None,
    projection_read_p95_ms: Optional[float] = None,
    max_unyielded_ms: Optional[float] = None,
) -> ConnectorOperationalMeasurement:
    evidence = _run_evidence(graph, domain_run_id)
    evidence_ids = {obj.id for obj in evidence}
    annotations = [
        obj for obj in graph.objects(type="semantic_annotation")
        if obj.data.get("evidence_id") in evidence_ids
        and obj.data.get("status") == "active"
    ]
    by_evidence: dict[str, int] = {evidence_id: 0 for evidence_id in evidence_ids}
    for annotation in annotations:
        evidence_id = str(annotation.data.get("evidence_id") or "")
        by_evidence[evidence_id] = by_evidence.get(evidence_id, 0) + 1

    events = graph.events[_event_start(graph, domain_run_id):]
    behavior_firings = sum(
        event.type in {"behavior.started", "relation_behavior.started"}
        for event in events
    )
    provider_calls = sum(
        1 for obj in graph.objects(type="capability_call")
        if (((obj.data.get("metadata") or {}).get("gmail") or {}).get("run_id"))
        == domain_run_id
    )
    artifact_refs = {
        str(obj.data.get("replay_payload_ref") or "")
        for obj in evidence
        if obj.data.get("replay_mode") == "artifact"
    }
    artifact_bytes = 0
    for ref in artifact_refs:
        path = artifact_path(artifact_store_dir, ref)
        if path.is_file():
            artifact_bytes += path.stat().st_size

    divisor = len(evidence) or 1
    return ConnectorOperationalMeasurement(
        domain_run_id=domain_run_id,
        evidence_items=len(evidence),
        durable_events=len(events),
        events_per_evidence=len(events) / divisor,
        annotations=len(annotations),
        max_annotations_for_one_evidence=max(by_evidence.values(), default=0),
        behavior_firings=behavior_firings,
        behavior_firings_per_evidence=behavior_firings / divisor,
        provider_calls=provider_calls,
        artifact_bytes=artifact_bytes,
        max_queue_depth=max_queue_depth,
        acknowledgement_ms=acknowledgement_ms,
        first_progress_ms=first_progress_ms,
        projection_read_p95_ms=projection_read_p95_ms,
        max_unyielded_ms=max_unyielded_ms,
    )


def operational_budget_violations(
    measurement: ConnectorOperationalMeasurement,
    policy: Optional[ConnectorOperationalPolicy] = None,
) -> list[str]:
    limits = policy or ConnectorOperationalPolicy(**operational_policy_payload())
    checks = {
        "evidence_items": (measurement.evidence_items, limits.fixture_items),
        "events_per_evidence": (
            measurement.events_per_evidence, limits.max_events_per_evidence
        ),
        "max_annotations_for_one_evidence": (
            measurement.max_annotations_for_one_evidence,
            limits.max_annotations_per_evidence,
        ),
        "behavior_firings_per_evidence": (
            measurement.behavior_firings_per_evidence,
            limits.max_behavior_firings_per_evidence,
        ),
        "provider_calls": (measurement.provider_calls, limits.max_provider_calls),
        "artifact_bytes": (measurement.artifact_bytes, limits.max_artifact_bytes),
        "max_queue_depth": (measurement.max_queue_depth, limits.max_queue_depth),
        "acknowledgement_ms": (measurement.acknowledgement_ms, limits.ack_latency_ms),
        "first_progress_ms": (measurement.first_progress_ms, limits.first_progress_ms),
        "projection_read_p95_ms": (
            measurement.projection_read_p95_ms, limits.projection_read_p95_ms
        ),
        "max_unyielded_ms": (measurement.max_unyielded_ms, limits.max_unyielded_ms),
    }
    violations = []
    for name, (observed, limit) in checks.items():
        if observed is None:
            violations.append(f"{name}: missing measurement")
        elif observed > limit:
            violations.append(f"{name}: {observed} > {limit}")
    return violations


__all__ = [
    "OPERATIONAL_POLICY_ID",
    "SUPERSEDED_POLICY_IDS",
    "ConnectorOperationalMeasurement",
    "measure_connector_run",
    "operational_budget_violations",
    "operational_policy_payload",
]
