"""Tools for semantic engagement observations and bounded session batches."""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from activegraph.packs import tool

from .object_types import AttentionObservation
from .settings import AttentionSettings


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()}"


def _object_by_field(graph, object_type: str, field: str, value: str):
    return next(
        (
            obj
            for obj in graph.objects(type=object_type)
            if obj.data.get(field) == value
        ),
        None,
    )


def record_attention_observation_fn(
    graph,
    *,
    observation_id: str,
    subject_ref: str,
    signal_type: str,
    subject_kind: str = "object",
    strength_milli: int = 1_000,
    context_key: str = "global",
    session_id: Optional[str] = None,
    opportunity_id: Optional[str] = None,
    active_ms: Optional[int] = None,
    occurred_at: Optional[str] = None,
    source: str = "client",
    explicit: bool = False,
    evidence_refs: Optional[list[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Record one idempotent semantic observation.

    Absence is censored unless the caller proves an opportunity: a
    ``nonresponse_window`` without ``opportunity_id`` fails closed.
    """
    if signal_type == "nonresponse_window" and not opportunity_id:
        raise ValueError("nonresponse_window requires opportunity_id")
    payload = AttentionObservation(
        observation_id=observation_id,
        subject_ref=subject_ref,
        subject_kind=subject_kind,
        signal_type=signal_type,
        strength_milli=strength_milli,
        context_key=context_key,
        session_id=session_id,
        opportunity_id=opportunity_id,
        active_ms=active_ms,
        occurred_at=occurred_at,
        source=source,
        explicit=explicit,
        evidence_refs=list(evidence_refs or []),
        metadata=dict(metadata or {}),
    ).model_dump()
    existing = _object_by_field(
        graph, "attention_observation", "observation_id", observation_id
    )
    if existing is not None:
        if dict(existing.data) != payload:
            raise ValueError(
                f"observation_id {observation_id!r} already has different data"
            )
        return {"ok": True, "created": False, "observation": existing}
    obj = graph.add_object("attention_observation", payload)
    return {"ok": True, "created": True, "observation": obj}


def record_interaction_batch_fn(
    graph,
    *,
    batch_id: str,
    session_id: str,
    batch_sequence: int,
    client_id: str,
    observations: list[dict[str, Any]],
    client_version: str = "",
    started_at: Optional[str] = None,
    ended_at: Optional[str] = None,
    active_duration_ms: int = 0,
    raw_event_count: int = 0,
    flush_reason: str = "manual",
    metadata: Optional[dict[str, Any]] = None,
    settings: Optional[AttentionSettings] = None,
) -> dict[str, Any]:
    """Flush a semantic-only batch atomically with respect to validation."""
    settings = settings or AttentionSettings()
    if len(observations) > settings.max_observations_per_batch:
        raise ValueError(
            f"interaction batch exceeds {settings.max_observations_per_batch} observations"
        )
    existing = _object_by_field(graph, "interaction_batch", "batch_id", batch_id)
    if existing is not None:
        for field, expected in (
            ("session_id", session_id),
            ("batch_sequence", batch_sequence),
            ("client_id", client_id),
        ):
            if existing.data.get(field) != expected:
                raise ValueError(
                    f"batch_id {batch_id!r} already belongs to a different {field}"
                )
        return {"ok": True, "created": False, "batch": existing, "observations": []}

    prepared: list[dict[str, Any]] = []
    for index, row in enumerate(observations):
        item = dict(row)
        observation_id = str(
            item.pop("observation_id", "")
            or _stable_id("attention_observation", session_id, batch_sequence, index)
        )
        payload = AttentionObservation(
            observation_id=observation_id,
            subject_ref=str(item.pop("subject_ref")),
            signal_type=str(item.pop("signal_type")),
            subject_kind=str(item.pop("subject_kind", "object")),
            strength_milli=int(item.pop("strength_milli", 1_000)),
            context_key=str(item.pop("context_key", "global")),
            session_id=session_id,
            opportunity_id=item.pop("opportunity_id", None),
            active_ms=item.pop("active_ms", None),
            occurred_at=item.pop("occurred_at", None),
            source=str(item.pop("source", "client")),
            explicit=bool(item.pop("explicit", False)),
            evidence_refs=list(item.pop("evidence_refs", [])),
            metadata={**dict(item.pop("metadata", {})), **item},
        ).model_dump()
        if payload["signal_type"] == "nonresponse_window" and not payload["opportunity_id"]:
            raise ValueError("nonresponse_window requires opportunity_id")
        collision = _object_by_field(
            graph, "attention_observation", "observation_id", observation_id
        )
        if collision is not None and dict(collision.data) != payload:
            raise ValueError(
                f"observation_id {observation_id!r} already has different data"
            )
        prepared.append(payload)

    created = []
    for payload in prepared:
        existing_observation = _object_by_field(
            graph,
            "attention_observation",
            "observation_id",
            payload["observation_id"],
        )
        if existing_observation is None:
            created.append(graph.add_object("attention_observation", payload))

    batch = graph.add_object(
        "interaction_batch",
        {
            "batch_id": batch_id,
            "session_id": session_id,
            "batch_sequence": batch_sequence,
            "client_id": client_id,
            "client_version": client_version,
            "started_at": started_at,
            "ended_at": ended_at,
            "active_duration_ms": active_duration_ms,
            "observation_ids": [row["observation_id"] for row in prepared],
            "raw_event_count": raw_event_count,
            "flush_reason": flush_reason,
            "privacy_mode": "semantic_only",
            "metadata": dict(metadata or {}),
        },
    )
    return {"ok": True, "created": True, "batch": batch, "observations": created}


@tool(name="record_attention_observation", description="Record one semantic attention signal.")
def record_attention_observation(
    graph,
    observation_id: str,
    subject_ref: str = "",
    signal_type: str = "opened",
    subject_kind: str = "object",
    strength_milli: int = 1_000,
    context_key: str = "global",
    session_id: Optional[str] = None,
    opportunity_id: Optional[str] = None,
    active_ms: Optional[int] = None,
    occurred_at: Optional[str] = None,
    source: str = "client",
    explicit: bool = False,
    evidence_refs: Optional[list[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return record_attention_observation_fn(
        graph,
        observation_id=observation_id,
        subject_ref=subject_ref,
        signal_type=signal_type,
        subject_kind=subject_kind,
        strength_milli=strength_milli,
        context_key=context_key,
        session_id=session_id,
        opportunity_id=opportunity_id,
        active_ms=active_ms,
        occurred_at=occurred_at,
        source=source,
        explicit=explicit,
        evidence_refs=evidence_refs,
        metadata=metadata,
    )


@tool(name="record_interaction_batch", description="Flush a bounded semantic-only client session batch.")
def record_interaction_batch(
    graph,
    batch_id: str,
    session_id: str = "",
    batch_sequence: int = 0,
    client_id: str = "client",
    observations: Optional[list[dict[str, Any]]] = None,
    client_version: str = "",
    started_at: Optional[str] = None,
    ended_at: Optional[str] = None,
    active_duration_ms: int = 0,
    raw_event_count: int = 0,
    flush_reason: str = "manual",
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return record_interaction_batch_fn(
        graph,
        batch_id=batch_id,
        session_id=session_id,
        batch_sequence=batch_sequence,
        client_id=client_id,
        observations=list(observations or []),
        client_version=client_version,
        started_at=started_at,
        ended_at=ended_at,
        active_duration_ms=active_duration_ms,
        raw_event_count=raw_event_count,
        flush_reason=flush_reason,
        metadata=metadata,
    )


TOOLS = [record_attention_observation, record_interaction_batch]

__all__ = [
    "TOOLS",
    "record_attention_observation_fn",
    "record_interaction_batch_fn",
]
