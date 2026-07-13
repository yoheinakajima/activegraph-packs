"""Deterministic, evidence-explainable importance and source-trust projectors."""

from __future__ import annotations

import hashlib
from typing import Any

from activegraph.packs import behavior

from .object_types import ImportanceVector, SourceTrustVector


POLICY_ID = "importance-trust.beta-evidence"
POLICY_VERSION = 1

# Engagement is evidence of salience, not truth. Weights are deliberately
# conservative and versioned; LLM judgment cannot raise either vector in v1.
_IMPORTANCE_WEIGHTS: dict[str, tuple[str, int]] = {
    "impression": ("opportunity", 0),
    "opened": ("engagement", 120),
    "active_dwell": ("engagement", 300),
    "revisited": ("recurrence", 220),
    "created": ("outcome", 350),
    "edited": ("outcome", 400),
    "replied": ("outcome", 650),
    "completed": ("outcome", 700),
    "dismissed": ("negative", -700),
    "archived": ("negative", -250),
    "explicit_important": ("explicit", 1_000),
    "explicit_not_important": ("explicit", -1_000),
    "nonresponse_window": ("negative", -180),
    "llm_judgment": ("model_only", 0),
}

_OUTCOME_WEIGHTS: dict[str, tuple[str, int]] = {
    "outcome.helped": ("helped", 1_000),
    "outcome.hurt": ("hurt", -1_000),
    "outcome.contradicted": ("contradicted", -1_000),
    "outcome.stale": ("stale", -600),
    "outcome.neutral": ("neutral", 0),
    "outcome.superseded": ("superseded", 0),
}


def _stable_key(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()}"


def _objects(reader, object_type: str):
    try:
        return list(reader.objects(type=object_type))
    except Exception:
        return []


def _importance_score(support: int, oppose: int) -> tuple[int, int, str]:
    effective = support + oppose
    score = ((1_000 + support) * 1_000) // (2_000 + effective)
    confidence = min(1_000, (effective * 1_000) // 4_000)
    if effective == 0:
        band = "unranked"
    elif score >= 650:
        band = "high"
    elif score <= 350:
        band = "low"
    else:
        band = "medium"
    return score, confidence, band


def _trust_score(support: int, challenge: int) -> tuple[int, int, str]:
    effective = support + challenge
    score = ((1_000 + support) * 1_000) // (2_000 + effective)
    confidence = min(1_000, (effective * 1_000) // 3_000)
    if effective == 0:
        verdict = "unproven"
    elif score >= 650:
        verdict = "supported"
    elif score <= 350:
        verdict = "harmful"
    else:
        verdict = "weak"
    return score, confidence, verdict


@behavior(
    name="importance_vector_projector",
    on=["object.created"],
    where={"object.type": "attention_observation"},
    view={"include_types": ["importance_vector"]},
    creates=["importance_vector"],
)
def importance_vector_projector(event, graph, ctx):
    wrapper = (event.payload or {}).get("object") or {}
    data = dict(wrapper.get("data") or {})
    observation_id = str(data.get("observation_id") or wrapper.get("id") or "")
    subject_ref = str(data.get("subject_ref") or "")
    if not observation_id or not subject_ref:
        return
    context_key = str(data.get("context_key") or "global")
    objective_ref = data.get("objective_ref") or None
    horizon_key = str(data.get("horizon_key") or "current")
    vector_key = _stable_key(
        "importance", subject_ref, context_key, objective_ref or "", horizon_key
    )
    existing = next(
        (
            obj for obj in _objects(ctx.view, "importance_vector")
            if obj.data.get("vector_key") == vector_key
        ),
        None,
    )
    observation_ids = list((existing.data if existing else {}).get("observation_ids") or [])
    if observation_id in observation_ids:
        return
    signal_type = str(data.get("signal_type") or "")
    feature, weight = _IMPORTANCE_WEIGHTS.get(signal_type, ("unknown", 0))
    strength = int(data.get("strength_milli") or 0)
    contribution = (abs(weight) * strength) // 1_000
    # Explicit signals only count when the caller marked the observation as an
    # explicit owner act. Merely naming the signal can never self-assert value.
    if signal_type.startswith("explicit_") and not bool(data.get("explicit")):
        contribution = 0
        feature = "unverified_explicit"
    prior = dict(existing.data) if existing else {}
    support = int(prior.get("support_milli") or 0)
    oppose = int(prior.get("oppose_milli") or 0)
    if weight > 0:
        support += contribution
    elif weight < 0:
        oppose += contribution
    features = dict(prior.get("features") or {})
    features[feature] = int(features.get(feature, 0)) + contribution
    observation_ids.append(observation_id)
    score, confidence, band = _importance_score(support, oppose)
    update = ImportanceVector(
        vector_key=vector_key,
        subject_ref=subject_ref,
        subject_kind=str(data.get("subject_kind") or "object"),
        context_key=context_key,
        objective_ref=objective_ref,
        horizon_key=horizon_key,
        score_milli=score,
        confidence_milli=confidence,
        priority_band=band,
        support_milli=support,
        oppose_milli=oppose,
        features=features,
        observation_ids=observation_ids,
        policy_id=POLICY_ID,
        policy_version=POLICY_VERSION,
        latest_observation_id=observation_id,
        metadata={"decay": "none", "llm_direct_weight": 0},
    ).model_dump()
    if existing is None:
        graph.add_object("importance_vector", update)
    else:
        graph.patch_object(existing.id, update)


def _trust_sources(payload: dict[str, Any]) -> list[dict[str, str]]:
    context = dict(payload.get("source_context") or {})
    rows = context.get("trust_sources")
    if isinstance(rows, list):
        return [dict(row) for row in rows if isinstance(row, dict) and row.get("source_ref")]
    source_ref = context.get("source_ref") or context.get("source_surface_id")
    if not source_ref:
        return []
    return [{
        "source_ref": str(source_ref),
        "source_kind": str(context.get("source_kind") or "source"),
        "domain": str(context.get("domain") or context.get("source_category") or "general"),
        "query_scope": str(context.get("query_scope") or "general"),
    }]


@behavior(
    name="source_trust_vector_projector",
    on=sorted(_OUTCOME_WEIGHTS),
    view={"include_types": ["source_trust_vector"]},
    creates=["source_trust_vector"],
)
def source_trust_vector_projector(event, graph, ctx):
    payload = dict(event.payload or {})
    feature, weight = _OUTCOME_WEIGHTS.get(event.type, ("unknown", 0))
    for source in _trust_sources(payload):
        source_ref = str(source["source_ref"])
        source_kind = str(source.get("source_kind") or "source")
        domain = str(source.get("domain") or "general")
        query_scope = str(source.get("query_scope") or "general")
        vector_key = _stable_key("source_trust", source_ref, domain, query_scope)
        existing = next(
            (
                obj for obj in _objects(ctx.view, "source_trust_vector")
                if obj.data.get("vector_key") == vector_key
            ),
            None,
        )
        event_ids = list((existing.data if existing else {}).get("outcome_event_ids") or [])
        if event.id in event_ids:
            continue
        prior = dict(existing.data) if existing else {}
        support = int(prior.get("support_milli") or 0)
        challenge = int(prior.get("challenge_milli") or 0)
        if weight > 0:
            support += weight
        elif weight < 0:
            challenge += abs(weight)
        features = dict(prior.get("features") or {})
        features[feature] = int(features.get(feature, 0)) + abs(weight)
        event_ids.append(event.id)
        score, confidence, verdict = _trust_score(support, challenge)
        update = SourceTrustVector(
            vector_key=vector_key,
            source_ref=source_ref,
            source_kind=source_kind,
            domain=domain,
            query_scope=query_scope,
            score_milli=score,
            confidence_milli=confidence,
            verdict=verdict,
            support_milli=support,
            challenge_milli=challenge,
            features=features,
            outcome_event_ids=event_ids,
            policy_id=POLICY_ID,
            policy_version=POLICY_VERSION,
            latest_outcome_event_id=event.id,
            metadata={"decay": "none", "self_assertion": "ignored"},
        ).model_dump()
        if existing is None:
            graph.add_object("source_trust_vector", update)
        else:
            graph.patch_object(existing.id, update)


BEHAVIORS = [importance_vector_projector, source_trust_vector_projector]

__all__ = ["BEHAVIORS", "POLICY_ID", "POLICY_VERSION"]
