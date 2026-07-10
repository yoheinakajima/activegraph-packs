"""Graph-visible mutation and explicit-horizon query capabilities for Usage.

The event log is the only projection input. Query helpers require an explicit
horizon event id and replay canonical event order through that event. No helper
reads ambient time; provider timestamps and logged source clocks are the only
inputs to historical coverage.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from activegraph import Event
from activegraph.packs import tool

from .settings import UsageSettings


SOURCE_CATEGORIES = (
    "communication",
    "rhythm",
    "ai_activity",
    "code_work",
    "local_knowledge",
    "tool_automation",
    "outcome_evaluation",
)
SOURCE_CATEGORY_SET = frozenset(SOURCE_CATEGORIES)
CONNECTION_PATHS = frozenset({"export", "mcp", "composio", "native", "local", "pack"})
PRIVACY_SCOPES = frozenset({"source", "account", "folder", "label", "workspace"})
SURFACE_STATUSES = frozenset({"connected", "settling", "settled", "stale", "revoked", "failed"})
EXPLICIT_LIFECYCLE_STATUSES = frozenset({"connected", "stale", "revoked", "failed"})
LIVE_CONNECTION_PATHS = frozenset({"mcp", "composio", "native", "pack"})

DEFAULT_GATE = {
    "gate_id": "usage.category.default",
    "gate_version": 1,
    "min_unique_events": 25,
    "min_coverage_days": 3,
    "allow_either": True,
}

_TERMINAL_OUTCOMES = frozenset({"outcome.helped", "outcome.hurt", "outcome.neutral"})


def validate_source_category(category: str) -> str:
    """Return a canonical category or fail instead of inventing a fallback."""
    if category not in SOURCE_CATEGORY_SET:
        raise ValueError(
            f"unknown source category {category!r}; expected one of "
            f"{', '.join(SOURCE_CATEGORIES)}"
        )
    return category


def _stable_id(prefix: str, *parts: Any) -> str:
    encoded = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()}"


def _emit_event(
    graph,
    event_type: str,
    payload: dict[str, Any],
    *,
    caused_by: Optional[str] = None,
) -> Event:
    event = Event(
        id=graph.ids.event(),
        type=event_type,
        payload=payload,
        actor="usage",
        caused_by=caused_by,
        timestamp=graph.clock.now(),
    )
    graph.emit(event)
    return event


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _require_timestamp(value: str, field_name: str) -> str:
    parsed = _parse_timestamp(value)
    if parsed is None:
        raise ValueError(f"{field_name} must be an ISO 8601 timestamp")
    return parsed.isoformat()


def _surface_logical_id(data: dict[str, Any]) -> Optional[str]:
    value = data.get("id", data.get("surface_id"))
    return str(value) if value else None


def _find_surface(graph, surface_id: str):
    matches = [
        obj
        for obj in graph.objects(type="connection_surface")
        if _surface_logical_id(obj.data or {}) == surface_id
    ]
    if len(matches) > 1:
        raise RuntimeError(f"multiple connection_surface objects claim id {surface_id!r}")
    return matches[0] if matches else None


def _find_gate(graph, gate_id: str, gate_version: int):
    matches = [
        obj
        for obj in graph.objects(type="settling_gate")
        if obj.data.get("gate_id") == gate_id
        and obj.data.get("gate_version") == gate_version
    ]
    if len(matches) > 1:
        raise RuntimeError(f"duplicate settling gate {gate_id}@{gate_version}")
    return matches[0] if matches else None


def ensure_default_gate_fn(
    graph,
    *,
    settings: Optional[UsageSettings] = None,
):
    """Create the immutable default gate once and reject semantic mutation."""
    configured = settings or UsageSettings()
    definition = {
        "definition_identity": _stable_id(
            "settling_gate",
            configured.default_gate_id,
            configured.default_gate_version,
            configured.min_unique_events,
            configured.min_coverage_days,
            configured.allow_either,
        ),
        "gate_id": configured.default_gate_id,
        "gate_version": configured.default_gate_version,
        "category": None,
        "min_unique_events": configured.min_unique_events,
        "min_coverage_days": configured.min_coverage_days,
        "allow_either": configured.allow_either,
        "active": True,
    }
    if (
        definition["gate_id"] == DEFAULT_GATE["gate_id"]
        and definition["gate_version"] == DEFAULT_GATE["gate_version"]
        and any(definition[key] != value for key, value in DEFAULT_GATE.items())
    ):
        raise ValueError(
            "usage.category.default@1 semantics are immutable; use a new gate version"
        )
    existing = _find_gate(
        graph,
        definition["gate_id"],
        definition["gate_version"],
    )
    if existing is not None:
        actual = {key: existing.data.get(key) for key in definition}
        if actual != definition:
            raise ValueError(
                f"settling gate {definition['gate_id']}@{definition['gate_version']} "
                "already exists with "
                "different semantics; create a new gate version"
            )
        return existing
    return graph.add_object("settling_gate", definition)


def _cursor_default() -> dict[str, Any]:
    return {
        "oldest_ingested_ref": None,
        "newest_ingested_ref": None,
        "cursor_version": 1,
    }


def connect_surface_fn(
    graph,
    surface_id: str,
    category: str,
    *,
    provider: Optional[dict[str, Any]] = None,
    path: str = "native",
    privacy_scope: str = "source",
    adapter: Optional[str] = None,
    acquisition_mode: str = "snapshot",
    cursor_state: Optional[dict[str, Any]] = None,
    is_fixture: bool = False,
    metadata: Optional[dict[str, Any]] = None,
    settings: Optional[UsageSettings] = None,
) -> dict[str, Any]:
    """Create or reconnect exactly one logical source surface.

    Reconnect emits a new connection fact but never creates a second surface.
    It also never directly marks a surface settled; gate evaluation owns that.
    """
    if not surface_id:
        raise ValueError("surface_id is required")
    category = validate_source_category(category)
    if path not in CONNECTION_PATHS:
        raise ValueError(f"unknown connection path {path!r}")
    if privacy_scope not in PRIVACY_SCOPES:
        raise ValueError(f"unknown privacy scope {privacy_scope!r}")
    if acquisition_mode not in {"snapshot", "backfill", "live"}:
        raise ValueError(f"unknown acquisition mode {acquisition_mode!r}")
    gate = ensure_default_gate_fn(graph, settings=settings)

    requested = {
        "surface_id": surface_id,
        "category": category,
        "provider": dict(provider or {}),
        "path": path,
        "adapter": adapter,
        "acquisition_mode": acquisition_mode,
        "status": "connected",
        "privacy_scope": privacy_scope,
        "cursor_state": dict(cursor_state or _cursor_default()),
        "is_fixture": bool(is_fixture),
        "events_seen": 0,
        "unique_evidence_count": 0,
        "first_seen": None,
        "last_seen": None,
        "gate_id": gate.data["gate_id"],
        "gate_version": gate.data["gate_version"],
        "settled_by": [],
        "settled_event_id": None,
        "metadata": dict(metadata or {}),
    }
    existing = _find_surface(graph, surface_id)
    if existing is None:
        surface = graph.add_object("connection_surface", requested)
        created = True
    else:
        current = existing.data or {}
        for immutable in ("category", "path", "privacy_scope", "acquisition_mode"):
            if current.get(immutable) != requested[immutable]:
                raise ValueError(
                    f"surface {surface_id!r} already has {immutable}="
                    f"{current.get(immutable)!r}; create a new surface identity"
                )
        updates: dict[str, Any] = {"status": "connected"}
        if provider is not None:
            updates["provider"] = dict(provider)
        if adapter is not None:
            updates["adapter"] = adapter
        if metadata is not None:
            updates["metadata"] = dict(metadata)
        if is_fixture != bool(current.get("is_fixture", False)):
            updates["is_fixture"] = bool(is_fixture)
        if cursor_state is not None:
            updates["cursor_state"] = dict(cursor_state)
        graph.patch_object(existing.id, updates, rationale="source surface reconnected")
        surface = graph.get_object(existing.id)
        created = False

    data = dict(surface.data)
    event = _emit_event(
        graph,
        "source.connected",
        {
            "surface_id": surface_id,
            "surface_object_id": surface.id,
            "category": data["category"],
            "provider": data.get("provider") or {},
            "path": data["path"],
            "adapter": data.get("adapter"),
            "acquisition_mode": data.get("acquisition_mode", "snapshot"),
            "status": "connected",
            "privacy_scope": data["privacy_scope"],
            "cursor_state": data.get("cursor_state") or _cursor_default(),
            "events_seen": int(data.get("events_seen", 0)),
            "unique_evidence_count": int(data.get("unique_evidence_count", 0)),
            "first_seen": data.get("first_seen"),
            "last_seen": data.get("last_seen"),
            "is_fixture": bool(data.get("is_fixture", False)),
            "metadata": data.get("metadata") or {},
            "gate_id": gate.data["gate_id"],
            "gate_version": gate.data["gate_version"],
            "settled_by": data.get("settled_by") or [],
            "settled_event_id": data.get("settled_event_id"),
            "reconnect": not created,
        },
    )
    return {
        "ok": True,
        "created": created,
        "surface_id": surface_id,
        "surface_object_id": surface.id,
        "connection_event_id": event.id,
        "gate_id": gate.data["gate_id"],
        "gate_version": gate.data["gate_version"],
    }


def set_surface_status_fn(
    graph,
    surface_id: str,
    status: str,
    *,
    reason: str = "",
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Record an explicit lifecycle fact; computed states cannot be forced."""
    if status not in EXPLICIT_LIFECYCLE_STATUSES:
        raise ValueError(
            "explicit lifecycle status must be connected, stale, revoked, or failed"
        )
    surface = _find_surface(graph, surface_id)
    if surface is None:
        raise ValueError(f"unknown connection surface {surface_id!r}")
    previous = surface.data.get("status")
    event = _emit_event(
        graph,
        "source.lifecycle_changed",
        {
            "surface_id": surface_id,
            "surface_object_id": surface.id,
            "category": surface.data["category"],
            "path": surface.data["path"],
            "previous_status": previous,
            "status": status,
            "reason": reason,
            "metadata": dict(metadata or {}),
        },
    )
    graph.patch_object(
        surface.id,
        {"status": status},
        caused_by=event.id,
        rationale=reason or f"source lifecycle changed to {status}",
    )
    return {"ok": True, "surface_id": surface_id, "status": status, "event_id": event.id}


def record_source_clock_fn(
    graph,
    surface_id: str,
    observed_at: str,
    *,
    clock_id: Optional[str] = None,
    is_fixture: bool = False,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Append an idempotent logged clock input for a live source surface."""
    surface = _find_surface(graph, surface_id)
    if surface is None:
        raise ValueError(f"unknown connection surface {surface_id!r}")
    if surface.data.get("acquisition_mode") != "live":
        raise ValueError("source.clock is valid only for acquisition_mode='live'")
    canonical_time = _require_timestamp(observed_at, "observed_at")
    identity = clock_id or _stable_id("source_clock", surface_id, canonical_time)
    existing = [
        event
        for event in graph.events
        if event.type == "source.clock"
        and (event.payload or {}).get("clock_id") == identity
    ]
    if existing:
        prior = existing[-1]
        if (prior.payload or {}).get("surface_id") != surface_id or (
            prior.payload or {}
        ).get("observed_at") != canonical_time:
            raise ValueError(f"clock_id {identity!r} already records a different fact")
        return {"ok": True, "created": False, "clock_id": identity, "event_id": prior.id}
    event = _emit_event(
        graph,
        "source.clock",
        {
            "clock_id": identity,
            "surface_id": surface_id,
            "category": surface.data["category"],
            "observed_at": canonical_time,
            "is_fixture": bool(is_fixture),
            "metadata": dict(metadata or {}),
        },
    )
    return {"ok": True, "created": True, "clock_id": identity, "event_id": event.id}


def record_usage_fn(
    graph,
    usage_identity: str,
    surface_id: str,
    interaction_type: str,
    *,
    evidence_identity: Optional[str] = None,
    provider_time: Optional[str] = None,
    count: int = 1,
    is_fixture: bool = False,
    provenance: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Record one idempotent neutral interaction fact."""
    if not usage_identity or not interaction_type:
        raise ValueError("usage_identity and interaction_type are required")
    if count < 1:
        raise ValueError("count must be positive")
    surface = _find_surface(graph, surface_id)
    if surface is None:
        raise ValueError(f"unknown connection surface {surface_id!r}")
    category = validate_source_category(surface.data["category"])
    canonical_time = _require_timestamp(provider_time, "provider_time") if provider_time else None
    requested = {
        "usage_identity": usage_identity,
        "source_surface_id": surface_id,
        "source_category": category,
        "interaction_type": interaction_type,
        "evidence_identity": evidence_identity,
        "provider_time": canonical_time,
        "count": count,
        "is_fixture": bool(is_fixture),
        "source_provenance": dict(provenance or {}),
        "metadata": dict(metadata or {}),
    }
    matches = [
        obj
        for obj in graph.objects(type="usage_record")
        if obj.data.get("usage_identity") == usage_identity
    ]
    if len(matches) > 1:
        raise RuntimeError(f"duplicate usage_record identity {usage_identity!r}")
    if matches:
        existing = matches[0]
        if _canonical_json(existing.data) != _canonical_json(requested):
            raise ValueError(
                f"usage_identity {usage_identity!r} already records a different fact"
            )
        event = next(
            (
                item
                for item in graph.events
                if item.type == "usage.recorded"
                and (item.payload or {}).get("usage_identity") == usage_identity
            ),
            None,
        )
        return {
            "ok": True,
            "created": False,
            "usage_record_id": existing.id,
            "event_id": event.id if event else None,
        }

    record = graph.add_object("usage_record", requested)
    graph.add_relation(record.id, surface.id, "usage_on_surface")
    event = _emit_event(
        graph,
        "usage.recorded",
        {**requested, "usage_record_id": record.id},
    )
    return {
        "ok": True,
        "created": True,
        "usage_record_id": record.id,
        "event_id": event.id,
    }


def _events_through(graph, event_horizon_event_id: str) -> list[Event]:
    if not event_horizon_event_id:
        raise ValueError("event_horizon_event_id is required")
    events = list(graph.events)
    indexes = [i for i, event in enumerate(events) if event.id == event_horizon_event_id]
    if not indexes:
        raise ValueError(f"unknown event horizon {event_horizon_event_id!r}")
    if len(indexes) > 1:
        raise ValueError(f"event horizon id is ambiguous: {event_horizon_event_id!r}")
    return events[: indexes[0] + 1]


def _surface_from_connected(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("surface")
    if isinstance(nested, dict):
        merged = dict(nested)
        for key, value in payload.items():
            if key != "surface":
                merged.setdefault(key, value)
        payload = merged
    surface_id = payload.get("surface_id", payload.get("id"))
    if not surface_id:
        raise ValueError("connection surface event is missing surface_id")
    category = validate_source_category(str(payload.get("category", "")))
    path = str(payload.get("path", ""))
    if path not in CONNECTION_PATHS:
        raise ValueError(f"surface {surface_id!r} has unknown connection path {path!r}")
    return {
        "id": str(surface_id),
        "surface_id": str(surface_id),
        "surface_object_id": payload.get("surface_object_id"),
        "category": category,
        "provider": payload.get("provider") or {},
        "path": path,
        "adapter": payload.get("adapter"),
        "acquisition_mode": payload.get("acquisition_mode", "snapshot"),
        "privacy_scope": payload.get("privacy_scope", "source"),
        "cursor_state": payload.get("cursor_state") or _cursor_default(),
        "events_seen": int(payload.get("events_seen", 0)),
        "unique_evidence_count": int(payload.get("unique_evidence_count", 0)),
        "first_seen": payload.get("first_seen"),
        "last_seen": payload.get("last_seen"),
        "is_fixture": bool(payload.get("is_fixture", False)),
        "metadata": payload.get("metadata") or {},
        "gate_id": payload.get("gate_id", DEFAULT_GATE["gate_id"]),
        "gate_version": int(payload.get("gate_version", DEFAULT_GATE["gate_version"])),
        "settled_by": payload.get("settled_by") or [],
        "settled_event_id": payload.get("settled_event_id"),
    }


def _coverage_from_times(values: Iterable[str]) -> dict[str, Any]:
    parsed = sorted(item for item in (_parse_timestamp(value) for value in values) if item)
    if not parsed:
        return {"earliest": None, "latest": None, "coverage_days": 0}
    return {
        "earliest": parsed[0].isoformat(),
        "latest": parsed[-1].isoformat(),
        "coverage_days": (parsed[-1].date() - parsed[0].date()).days,
    }


def _empty_counter() -> dict[str, Any]:
    return {"total": 0, "by_kind": {}}


def _increment_counter(counter: dict[str, Any], kind: str, amount: int = 1) -> None:
    counter["total"] += amount
    counter["by_kind"][kind] = counter["by_kind"].get(kind, 0) + amount


def _qualifying_evidence(payload: dict[str, Any], surface: dict[str, Any]) -> bool:
    return bool(
        payload.get("evidence_identity")
        and payload.get("source_surface_id") == surface["id"]
        and payload.get("source_ref")
        and payload.get("source_category") == surface["category"]
        and payload.get("connection_path") == surface["path"]
        and payload.get("importer_id")
        and payload.get("importer_version")
        and not payload.get("invalidated", False)
        and not payload.get("is_fixture", False)
        and not surface.get("is_fixture", False)
    )


def project_usage_fn(graph, event_horizon_event_id: str) -> dict[str, Any]:
    """Replay neutral usage facts through one required event horizon."""
    events = _events_through(graph, event_horizon_event_id)
    surfaces: dict[str, dict[str, Any]] = {}
    lifecycle: dict[str, dict[str, Any]] = {}
    evidence: dict[tuple[str, str], dict[str, Any]] = {}
    clocks: dict[str, dict[str, dict[str, Any]]] = {}
    usage_records: dict[str, dict[str, Any]] = {}
    settled_events: dict[tuple[str, str, int], dict[str, Any]] = {}
    evidence_events_seen: dict[str, int] = {}
    gates: dict[tuple[str, int], dict[str, Any]] = {
        (DEFAULT_GATE["gate_id"], DEFAULT_GATE["gate_version"]): dict(DEFAULT_GATE)
    }
    outcomes: list[dict[str, Any]] = []

    for ordinal, event in enumerate(events):
        payload = dict(event.payload or {})
        if event.type == "object.created":
            wrapper = payload.get("object") or {}
            object_type = wrapper.get("type")
            data = dict(wrapper.get("data") or {})
            if object_type == "settling_gate":
                gate_id = data.get("gate_id")
                gate_version = data.get("gate_version")
                if gate_id and isinstance(gate_version, int):
                    key = (gate_id, gate_version)
                    if key in gates:
                        semantic_keys = tuple(DEFAULT_GATE)
                        prior_semantics = {name: gates[key].get(name) for name in semantic_keys}
                        new_semantics = {name: data.get(name) for name in semantic_keys}
                        if prior_semantics != new_semantics:
                            raise ValueError(
                                f"gate {gate_id}@{gate_version} changed semantics"
                            )
                    gates[key] = data
            elif object_type == "usage_record":
                usage_id = data.get("usage_identity")
                if usage_id:
                    usage_records.setdefault(str(usage_id), {**data, "event_id": event.id})
        elif event.type == "source.connected":
            surface = _surface_from_connected(payload)
            sid = surface["id"]
            prior = surfaces.get(sid, {})
            surfaces[sid] = {**prior, **surface}
            lifecycle[sid] = {
                "status": "connected",
                "reason": "",
                "event_id": event.id,
                "ordinal": ordinal,
            }
        elif event.type == "source.lifecycle_changed":
            sid = str(payload.get("surface_id") or "")
            status = str(payload.get("status") or "")
            if status not in EXPLICIT_LIFECYCLE_STATUSES:
                raise ValueError(f"invalid explicit lifecycle status {status!r}")
            lifecycle[sid] = {
                "status": status,
                "reason": payload.get("reason", ""),
                "event_id": event.id,
                "ordinal": ordinal,
            }
        elif event.type == "source.cursor_advanced":
            sid = str(payload.get("source_surface_id") or payload.get("surface_id") or "")
            if sid in surfaces:
                surfaces[sid]["cursor_state"] = {
                    "oldest_ingested_ref": payload.get("oldest_ingested_ref"),
                    "newest_ingested_ref": payload.get("newest_ingested_ref"),
                    "cursor_version": int(payload.get("cursor_version", 1)),
                }
        elif event.type == "source.event_ingested":
            sid = str(payload.get("source_surface_id") or "")
            identity = str(payload.get("evidence_identity") or "")
            if sid and identity:
                evidence_events_seen[sid] = evidence_events_seen.get(sid, 0) + 1
                category = validate_source_category(str(payload.get("source_category") or ""))
                revision = {
                    **payload,
                    "source_category": category,
                    "event_id": event.id,
                    "event_timestamp": event.timestamp,
                    "ordinal": ordinal,
                }
                key = (sid, identity)
                prior = evidence.get(key)
                if prior is None or int(revision.get("revision_number", 0)) >= int(
                    prior.get("revision_number", 0)
                ):
                    evidence[key] = revision
        elif event.type in ("source.event_invalidated", "source.evidence_invalidated"):
            sid = str(payload.get("source_surface_id") or payload.get("surface_id") or "")
            identity = str(payload.get("evidence_identity") or "")
            key = (sid, identity)
            if key in evidence:
                evidence[key] = {**evidence[key], "invalidated": True, "ordinal": ordinal}
        elif event.type == "source.clock":
            sid = str(payload.get("surface_id") or "")
            clock_id = str(payload.get("clock_id") or event.id)
            clocks.setdefault(sid, {}).setdefault(
                clock_id,
                {**payload, "event_id": event.id, "event_timestamp": event.timestamp},
            )
        elif event.type == "usage.recorded":
            usage_id = str(payload.get("usage_identity") or "")
            if usage_id:
                usage_records.setdefault(usage_id, {**payload, "event_id": event.id})
        elif event.type == "source.settled":
            sid = str(payload.get("surface_id") or payload.get("source_surface_id") or "")
            gate_id = str(payload.get("gate_id") or DEFAULT_GATE["gate_id"])
            gate_version = int(payload.get("gate_version", DEFAULT_GATE["gate_version"]))
            settled_events.setdefault(
                (sid, gate_id, gate_version),
                {**payload, "event_id": event.id, "ordinal": ordinal},
            )
        elif event.type.startswith("outcome."):
            outcomes.append({**payload, "event_type": event.type, "event_id": event.id})

    by_surface: dict[str, dict[str, Any]] = {}
    surface_rows: list[dict[str, Any]] = []
    for sid in sorted(surfaces):
        surface = surfaces[sid]
        gate_key = (surface["gate_id"], surface["gate_version"])
        gate = gates.get(gate_key)
        if gate is None:
            raise ValueError(f"surface {sid!r} references unknown gate {gate_key[0]}@{gate_key[1]}")
        current = [
            item
            for (surface_id, _), item in evidence.items()
            if surface_id == sid and _qualifying_evidence(item, surface)
        ]
        provider_times = [
            item["provider_time"]
            for item in current
            if _parse_timestamp(item.get("provider_time"))
        ]
        coverage_times = list(provider_times)
        if current and surface.get("acquisition_mode") == "live":
            coverage_times.extend(
                item.get("provider_time") or item.get("event_timestamp")
                for item in current
                if _parse_timestamp(item.get("provider_time") or item.get("event_timestamp"))
            )
            coverage_times.extend(
                item.get("observed_at") or item.get("event_timestamp")
                for item in clocks.get(sid, {}).values()
                if not item.get("is_fixture", False)
                and _parse_timestamp(item.get("observed_at") or item.get("event_timestamp"))
            )
        coverage = _coverage_from_times(coverage_times)
        unique_events = len(current)
        volume_passed = unique_events >= int(gate["min_unique_events"])
        coverage_passed = coverage["coverage_days"] >= int(gate["min_coverage_days"])
        gate_passed = (
            volume_passed or coverage_passed
            if gate.get("allow_either", True)
            else volume_passed and coverage_passed
        )
        passed_by = (
            "both"
            if volume_passed and coverage_passed
            else "volume"
            if volume_passed
            else "coverage"
            if coverage_passed
            else None
        )
        passed_thresholds = [
            name
            for name, did_pass in (
                ("volume", volume_passed),
                ("coverage", coverage_passed),
            )
            if did_pass
        ]
        contribution_key = (sid, gate_key[0], gate_key[1])
        settled = settled_events.get(contribution_key)
        explicit = lifecycle.get(sid, {"status": "connected", "reason": ""})
        if explicit["status"] in {"stale", "revoked", "failed"}:
            status = explicit["status"]
        elif gate_passed:
            status = "settled"
        elif settled is not None:
            status = "stale"
        elif current:
            status = "settling"
        else:
            status = "connected"

        fixture_visible = sum(
            1
            for (surface_id, _), item in evidence.items()
            if surface_id == sid and item.get("is_fixture", False)
        )
        all_visible = evidence_events_seen.get(sid, 0)
        stats = {
            "surface_id": sid,
            "category": surface["category"],
            "unique_evidence": unique_events,
            "visible_evidence_revisions": all_visible,
            "fixture_evidence": fixture_visible,
            **coverage,
            "volume_passed": volume_passed,
            "coverage_passed": coverage_passed,
        }
        settlement = {
            "surface_id": sid,
            "category": surface["category"],
            "status": status,
            "gate_id": gate_key[0],
            "gate_version": gate_key[1],
            "min_unique_events": gate["min_unique_events"],
            "min_coverage_days": gate["min_coverage_days"],
            "allow_either": bool(gate.get("allow_either", True)),
            "passed": gate_passed,
            "passed_by": passed_by,
            "settled_event_id": settled.get("event_id") if settled else None,
            "lifecycle_event_id": explicit.get("event_id"),
            "lifecycle_reason": explicit.get("reason", ""),
        }
        row = {
            **surface,
            "status": status,
            "events_seen": all_visible,
            "unique_evidence_count": unique_events,
            "first_seen": coverage["earliest"],
            "last_seen": coverage["latest"],
            "settled_by": passed_thresholds if status == "settled" else [],
            "settled_event_id": settled.get("event_id") if settled else None,
            "coverage": stats,
            "settlement": settlement,
        }
        by_surface[sid] = stats
        surface_rows.append(row)

    by_category: dict[str, dict[str, Any]] = {}
    for category in SOURCE_CATEGORIES:
        category_surfaces = [row for row in surface_rows if row["category"] == category]
        ids = {
            (sid, identity)
            for (sid, identity), item in evidence.items()
            if sid in {row["id"] for row in category_surfaces}
            and _qualifying_evidence(item, surfaces[sid])
        }
        times: list[str] = []
        for row in category_surfaces:
            if row["coverage"]["earliest"]:
                times.append(row["coverage"]["earliest"])
            if row["coverage"]["latest"]:
                times.append(row["coverage"]["latest"])
        by_category[category] = {
            "category": category,
            "unique_evidence": len(ids),
            **_coverage_from_times(times),
            "surfaces": len(category_surfaces),
            "settled_surfaces": sum(
                row["status"] == "settled" for row in category_surfaces
            ),
        }

    usage_by_surface = {sid: _empty_counter() for sid in surfaces}
    usage_by_category = {category: _empty_counter() for category in SOURCE_CATEGORIES}
    qualifying_usage = []
    for usage_id in sorted(usage_records):
        record = usage_records[usage_id]
        sid = str(record.get("source_surface_id") or record.get("surface_id") or "")
        category = record.get("source_category") or record.get("category")
        if category:
            category = validate_source_category(str(category))
        if record.get("is_fixture", False) or sid not in surfaces:
            continue
        if surfaces[sid].get("is_fixture", False):
            continue
        category = category or surfaces[sid]["category"]
        kind = str(record.get("interaction_type") or record.get("kind") or "interaction")
        amount = int(record.get("count", 1))
        _increment_counter(usage_by_surface.setdefault(sid, _empty_counter()), kind, amount)
        _increment_counter(usage_by_category.setdefault(category, _empty_counter()), kind, amount)
        qualifying_usage.append({**record, "usage_identity": usage_id})

    outcome_by_surface = {sid: _empty_counter() for sid in surfaces}
    outcome_by_category = {category: _empty_counter() for category in SOURCE_CATEGORIES}
    seen_terminal: set[str] = set()
    seen_outcome_keys: set[str] = set()
    qualifying_outcomes = []
    for outcome in outcomes:
        if outcome.get("is_fixture", False):
            continue
        event_type = outcome["event_type"]
        if event_type in _TERMINAL_OUTCOMES:
            evaluation_id = str(outcome.get("evaluation_id") or "")
            if evaluation_id and evaluation_id in seen_terminal:
                continue
            if evaluation_id:
                seen_terminal.add(evaluation_id)
            key = f"terminal:{evaluation_id or outcome['event_id']}"
        else:
            key = str(outcome.get("contribution_key") or outcome["event_id"])
        if key in seen_outcome_keys:
            continue
        seen_outcome_keys.add(key)
        sid = str(outcome.get("source_surface_id") or outcome.get("surface_id") or "")
        if sid in surfaces and surfaces[sid].get("is_fixture", False):
            continue
        category = outcome.get("source_category") or outcome.get("category")
        if category:
            category = validate_source_category(str(category))
        elif sid in surfaces:
            category = surfaces[sid]["category"]
        kind = event_type.removeprefix("outcome.")
        if sid in surfaces and not surfaces[sid].get("is_fixture", False):
            _increment_counter(outcome_by_surface.setdefault(sid, _empty_counter()), kind)
        if category in SOURCE_CATEGORY_SET:
            _increment_counter(outcome_by_category.setdefault(category, _empty_counter()), kind)
        qualifying_outcomes.append(outcome)

    return {
        "event_horizon_event_id": event_horizon_event_id,
        "surfaces": surface_rows,
        "coverage": {"by_surface": by_surface, "by_category": by_category},
        "usage": {
            "total": sum(int(record.get("count", 1)) for record in qualifying_usage),
            "by_surface": usage_by_surface,
            "by_category": usage_by_category,
        },
        "outcomes": {
            "total": len(qualifying_outcomes),
            "by_surface": outcome_by_surface,
            "by_category": outcome_by_category,
        },
    }


def list_surfaces_fn(graph, event_horizon_event_id: str) -> list[dict[str, Any]]:
    return project_usage_fn(graph, event_horizon_event_id)["surfaces"]


def get_settlement_fn(
    graph,
    event_horizon_event_id: str,
    surface_id: Optional[str] = None,
) -> Any:
    rows = project_usage_fn(graph, event_horizon_event_id)["surfaces"]
    settlements = [row["settlement"] for row in rows]
    if surface_id is None:
        return settlements
    return next((row for row in settlements if row["surface_id"] == surface_id), None)


def get_coverage_fn(graph, event_horizon_event_id: str) -> dict[str, Any]:
    return project_usage_fn(graph, event_horizon_event_id)["coverage"]


def get_usage_stats_fn(graph, event_horizon_event_id: str) -> dict[str, Any]:
    return project_usage_fn(graph, event_horizon_event_id)["usage"]


def get_outcome_stats_fn(graph, event_horizon_event_id: str) -> dict[str, Any]:
    return project_usage_fn(graph, event_horizon_event_id)["outcomes"]


@tool(
    name="connect_source_surface",
    description="Create or reconnect one canonical source surface and record the connection fact.",
    deterministic=True,
)
def connect_source_surface(
    graph,
    surface_id: str,
    category: str = "",
    provider: Optional[dict[str, Any]] = None,
    path: str = "native",
    adapter: Optional[str] = None,
    acquisition_mode: str = "snapshot",
    privacy_scope: str = "source",
    is_fixture: bool = False,
) -> dict[str, Any]:
    return connect_surface_fn(
        graph,
        surface_id,
        category,
        provider=provider,
        path=path,
        adapter=adapter,
        acquisition_mode=acquisition_mode,
        privacy_scope=privacy_scope,
        is_fixture=is_fixture,
    )


@tool(
    name="set_source_surface_status",
    description="Record an explicit connected, stale, revoked, or failed source lifecycle fact.",
    deterministic=True,
)
def set_source_surface_status(
    graph,
    surface_id: str,
    status: str = "",
    reason: str = "",
) -> dict[str, Any]:
    return set_surface_status_fn(graph, surface_id, status, reason=reason)


@tool(
    name="record_source_clock",
    description="Record an explicit clock input for historical live-source coverage.",
    deterministic=True,
)
def record_source_clock(
    graph,
    surface_id: str,
    observed_at: str = "",
    clock_id: Optional[str] = None,
    is_fixture: bool = False,
) -> dict[str, Any]:
    return record_source_clock_fn(
        graph,
        surface_id,
        observed_at,
        clock_id=clock_id,
        is_fixture=is_fixture,
    )


@tool(
    name="record_usage",
    description="Record one idempotent interaction fact for a connected source surface.",
    deterministic=True,
)
def record_usage(
    graph,
    usage_identity: str,
    surface_id: str = "",
    interaction_type: str = "interaction",
    evidence_identity: Optional[str] = None,
    provider_time: Optional[str] = None,
    count: int = 1,
    is_fixture: bool = False,
) -> dict[str, Any]:
    return record_usage_fn(
        graph,
        usage_identity,
        surface_id,
        interaction_type,
        evidence_identity=evidence_identity,
        provider_time=provider_time,
        count=count,
        is_fixture=is_fixture,
    )


@tool(
    name="query_usage",
    description="Read the complete neutral projection at an explicit event horizon.",
    deterministic=True,
)
def query_usage(graph, event_horizon_event_id: str) -> dict[str, Any]:
    return project_usage_fn(graph, event_horizon_event_id)


@tool(
    name="list_source_surfaces",
    description="List source surfaces and their deterministic state at an explicit event horizon.",
    deterministic=True,
)
def list_source_surfaces(graph, event_horizon_event_id: str) -> list[dict[str, Any]]:
    return list_surfaces_fn(graph, event_horizon_event_id)


@tool(
    name="get_settlement_state",
    description="Read named and versioned source settlement facts at an explicit event horizon.",
    deterministic=True,
)
def get_settlement_state(
    graph,
    event_horizon_event_id: str,
    surface_id: Optional[str] = None,
) -> Any:
    return get_settlement_fn(graph, event_horizon_event_id, surface_id)


@tool(
    name="get_coverage_statistics",
    description="Read unique evidence and historical-span statistics at an explicit event horizon.",
    deterministic=True,
)
def get_coverage_statistics(graph, event_horizon_event_id: str) -> dict[str, Any]:
    return get_coverage_fn(graph, event_horizon_event_id)


@tool(
    name="get_usage_statistics",
    description="Read interaction statistics at an explicit event horizon.",
    deterministic=True,
)
def get_usage_statistics(graph, event_horizon_event_id: str) -> dict[str, Any]:
    return get_usage_stats_fn(graph, event_horizon_event_id)


@tool(
    name="get_outcome_statistics",
    description="Read terminal and maintenance outcome tallies at an explicit event horizon.",
    deterministic=True,
)
def get_outcome_statistics(graph, event_horizon_event_id: str) -> dict[str, Any]:
    return get_outcome_stats_fn(graph, event_horizon_event_id)


TOOLS = [
    connect_source_surface,
    set_source_surface_status,
    record_source_clock,
    record_usage,
    query_usage,
    list_source_surfaces,
    get_settlement_state,
    get_coverage_statistics,
    get_usage_statistics,
    get_outcome_statistics,
]


# Descriptive host-function aliases used by product integrations.
list_connection_surfaces_fn = list_surfaces_fn
get_settlement_state_fn = get_settlement_fn
get_coverage_statistics_fn = get_coverage_fn
get_usage_statistics_fn = get_usage_stats_fn
get_outcome_statistics_fn = get_outcome_stats_fn


__all__ = [
    "SOURCE_CATEGORIES",
    "DEFAULT_GATE",
    "TOOLS",
    "validate_source_category",
    "ensure_default_gate_fn",
    "connect_surface_fn",
    "set_surface_status_fn",
    "record_source_clock_fn",
    "record_usage_fn",
    "project_usage_fn",
    "list_connection_surfaces_fn",
    "get_settlement_state_fn",
    "get_coverage_statistics_fn",
    "get_usage_statistics_fn",
    "get_outcome_statistics_fn",
    "list_surfaces_fn",
    "get_settlement_fn",
    "get_coverage_fn",
    "get_usage_stats_fn",
    "get_outcome_stats_fn",
]
