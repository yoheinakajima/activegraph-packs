"""Provider-neutral integration profiles and explorer receipts.

This is the reusable connector chassis from INTEGRATION_DOCTRINE.  Route
adapters (Composio, MCP, native) supply observations; service packs supply
canonical operations and data semantics.  Understanding is keyed to
``(service, account)`` and survives route changes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from .object_types import AggregatorProfile, IntegrationExploration, IntegrationProfile


def stable_integration_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part).strip().lower() for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()}"


def shape_fingerprint(value: Any) -> str:
    """Hash structure and scalar kinds, excluding content and ordering noise."""

    def _shape(item: Any) -> Any:
        if isinstance(item, dict):
            return {str(key): _shape(item[key]) for key in sorted(item)}
        if isinstance(item, list):
            variants = {_canonical(_shape(child)) for child in item[:25]}
            return [json.loads(row) for row in sorted(variants)]
        if item is None:
            return "null"
        if isinstance(item, bool):
            return "bool"
        if isinstance(item, int):
            return "int"
        if isinstance(item, float):
            return "float"
        return "string"

    return "sha256:" + hashlib.sha256(_canonical(_shape(value)).encode("utf-8")).hexdigest()


def safe_sha256_fingerprint(value: str | bytes) -> str:
    """Return a useful SHA-256 receipt that secret sanitizers preserve."""

    encoded = value.encode("utf-8") if isinstance(value, str) else value
    digest = hashlib.sha256(encoded).hexdigest()
    return "sha256:" + ".".join(
        digest[index:index + 8] for index in range(0, len(digest), 8)
    )


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ensure_aggregator_profile_fn(
    graph,
    *,
    aggregator: str,
    user_ref: str,
    auth_state: str,
    available_services: Optional[list[str]] = None,
    enabled_services: Optional[list[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
    reader=None,
):
    view = reader or graph
    identity = stable_integration_id("aggregator", aggregator, user_ref)
    matches = [
        obj for obj in view.objects(type="aggregator_profile")
        if (obj.data or {}).get("profile_identity") == identity
    ]
    if len(matches) > 1:
        raise RuntimeError(f"duplicate aggregator profile {identity}")
    requested = AggregatorProfile(
        profile_identity=identity,
        aggregator=aggregator,
        user_ref=user_ref,
        auth_state=auth_state,
        available_services=sorted(set(available_services or [])),
        enabled_services=sorted(set(enabled_services or [])),
        metadata=dict(metadata or {}),
    ).model_dump()
    if not matches:
        return graph.add_object("aggregator_profile", requested)
    existing = matches[0]
    updates = {key: value for key, value in requested.items() if existing.data.get(key) != value}
    if updates:
        graph.patch_object(existing.id, updates)
    return graph.get_object(existing.id)


def latest_integration_profile(graph, service: str, account_ref: str, *, reader=None):
    view = reader or graph
    identity = stable_integration_id("integration", service, account_ref)
    matches = [
        obj for obj in view.objects(type="integration_profile")
        if (obj.data or {}).get("profile_identity") == identity
    ]
    if not matches:
        return None
    matches.sort(key=lambda obj: int((obj.data or {}).get("profile_version") or 0))
    highest = int((matches[-1].data or {}).get("profile_version") or 0)
    if sum(1 for obj in matches if int((obj.data or {}).get("profile_version") or 0) == highest) > 1:
        raise RuntimeError(f"duplicate integration profile version {identity}@{highest}")
    return matches[-1]


def active_integration_profile(graph, service: str, account_ref: str, *, reader=None):
    latest = latest_integration_profile(graph, service, account_ref, reader=reader)
    return latest if latest is not None and (latest.data or {}).get("status") == "active" else None


def record_integration_profile_fn(
    graph,
    *,
    service: str,
    account_ref: str,
    routes: list[dict[str, Any]],
    scopes_granted: Optional[list[str]] = None,
    scopes_available: Optional[list[str]] = None,
    facets: Optional[list[str]] = None,
    capability_inventory: Optional[list[dict[str, Any]]] = None,
    data_topology: Optional[dict[str, Any]] = None,
    signal_map: Optional[list[dict[str, Any]]] = None,
    claims: Optional[list[dict[str, Any]]] = None,
    health: Optional[dict[str, Any]] = None,
    exploration_receipts: Optional[list[str]] = None,
    account_display: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    profile_status: str = "active",
    reader=None,
):
    """Create a new profile version only when its semantic body changes."""

    identity = stable_integration_id("integration", service, account_ref)
    current = latest_integration_profile(graph, service, account_ref, reader=reader)
    version = int((current.data or {}).get("profile_version", 0)) + 1 if current else 1
    body = {
        "service": service,
        "account_ref": account_ref,
        "account_display": account_display,
        "routes": routes,
        "scopes_granted": sorted(set(scopes_granted or [])),
        "scopes_available": sorted(set(scopes_available or [])),
        "facets": list(dict.fromkeys(facets or [])),
        "capability_inventory": capability_inventory or [],
        "data_topology": data_topology or {},
        "signal_map": signal_map or [],
        "claims": claims or [],
        "health": health or {},
        "exploration_receipts": list(dict.fromkeys(exploration_receipts or [])),
        "metadata": metadata or {},
    }
    if current:
        comparable = {key: (current.data or {}).get(key) for key in body}
        if _canonical(comparable) == _canonical(body) and (current.data or {}).get("status") == profile_status:
            return current, False
    requested = IntegrationProfile(
        profile_identity=identity,
        profile_version=version,
        status=profile_status,
        supersedes_id=current.id if current else None,
        **body,
    ).model_dump()
    if current:
        graph.patch_object(current.id, {"status": "superseded"})
    created = graph.add_object("integration_profile", requested)
    if current:
        graph.add_relation(created.id, current.id, "integration_supersedes")
    return created, True


def correct_integration_claim_fn(
    graph,
    *,
    profile_id: str,
    claim_key: str,
    value: Any,
    actor: str,
    reason: str,
):
    """Supersede one machine claim with a recorded owner/operator claim."""

    profile = graph.get_object(profile_id)
    if profile is None or profile.type != "integration_profile":
        raise ValueError("integration profile not found")
    if (profile.data or {}).get("status") != "active":
        raise ValueError("only the active integration profile can be corrected")
    claims = [
        dict(claim)
        for claim in profile.data.get("claims") or []
        if claim.get("claim_key") != claim_key
    ]
    claims.append(
        {
            "claim_key": claim_key,
            "value": value,
            "confidence": 1.0,
            "freshness": "current",
            "provenance": [profile.id],
            "classification_source": "operator",
            "asserted_by": actor,
            "observed_at_event_id": None,
            "metadata": {"reason": reason},
        }
    )
    data = profile.data or {}
    corrected, created = record_integration_profile_fn(
        graph,
        service=data["service"],
        account_ref=data["account_ref"],
        account_display=data.get("account_display"),
        routes=list(data.get("routes") or []),
        scopes_granted=list(data.get("scopes_granted") or []),
        scopes_available=list(data.get("scopes_available") or []),
        facets=list(data.get("facets") or []),
        capability_inventory=list(data.get("capability_inventory") or []),
        data_topology=dict(data.get("data_topology") or {}),
        signal_map=list(data.get("signal_map") or []),
        claims=claims,
        health=dict(data.get("health") or {}),
        exploration_receipts=list(data.get("exploration_receipts") or []),
        metadata={**dict(data.get("metadata") or {}), "last_corrected_by": actor},
    )
    return {"ok": True, "created": created, "profile_id": corrected.id}


def record_exploration_fn(
    graph,
    *,
    service: str,
    account_ref: str,
    route: str,
    probe_call_ids: list[str],
    budget: int,
    status: str,
    observed_shape: Any = None,
    profile_id: Optional[str] = None,
    structural_only: bool = True,
    injection_flags: Optional[list[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
    reader=None,
):
    identity = stable_integration_id(
        "exploration", service, account_ref, route, ",".join(probe_call_ids)
    )
    view = reader or graph
    existing = next(
        (
            obj for obj in view.objects(type="integration_exploration")
            if (obj.data or {}).get("receipt_identity") == identity
        ),
        None,
    )
    if existing:
        return existing, False
    receipt = graph.add_object(
        "integration_exploration",
        IntegrationExploration(
            receipt_identity=identity,
            service=service,
            account_ref=account_ref,
            route=route,
            profile_id=profile_id,
            probe_call_ids=probe_call_ids,
            budget=budget,
            structural_only=structural_only,
            shape_fingerprint=(shape_fingerprint(observed_shape) if observed_shape is not None else None),
            status=status,
            injection_flags=sorted(set(injection_flags or [])),
            metadata=dict(metadata or {}),
        ).model_dump(),
    )
    if profile_id:
        graph.add_relation(profile_id, receipt.id, "profile_explored_by")
    return receipt, True


__all__ = [
    "stable_integration_id",
    "shape_fingerprint",
    "safe_sha256_fingerprint",
    "ensure_aggregator_profile_fn",
    "active_integration_profile",
    "latest_integration_profile",
    "record_integration_profile_fn",
    "correct_integration_claim_fn",
    "record_exploration_fn",
]
