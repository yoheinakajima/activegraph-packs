"""Durable graph-state implementation of the conversation family contract."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

from activegraph import Event
from activegraph.packs import behavior

from .hygiene import FILTER_VERSION, HygieneResult
from .settings import CommunicationSettings


FAMILY_PROJECTOR_VERSION = "communication.family@0.1.0"
LLM_FACETS = ("event_mention", "relation_mention")


def stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()}"


def _find(reader, object_type: str, field: str, value: str):
    return next(
        (obj for obj in reader.objects(type=object_type) if obj.data.get(field) == value),
        None,
    )


def _patch(graph, target: str, updates: dict[str, Any]) -> None:
    if not updates:
        return
    if hasattr(graph, "objects"):
        graph.patch_object(target, updates, rationale="refresh conversation family state")
    else:
        graph.patch_object(target, updates)


def _emit_event(graph, event_type: str, payload: dict[str, Any], actor: str):
    if not hasattr(graph, "ids"):
        return graph.emit(event_type, payload)
    event = Event(
        id=graph.ids.event(), type=event_type, payload=payload, actor=actor,
        timestamp=graph.clock.now(),
    )
    graph.emit(event)
    return event


def _active_profile(reader):
    profiles = [
        obj for obj in reader.objects(type="extraction_profile")
        if obj.data.get("status") == "active"
    ]
    return max(profiles, key=lambda obj: int(obj.data.get("version") or 0), default=None)


def materialize_conversation_message_fn(
    graph,
    *,
    reader,
    source_surface_id: str,
    service: str,
    account_ref: str,
    provider_thread_id: str,
    provider_message_id: str,
    provider_revision_ref: str,
    evidence_id: str,
    evidence_revision_id: str,
    subject: str,
    sender: Optional[str],
    recipients: list[str],
    cc: list[str],
    sent_at: Optional[str],
    direction: str,
    message_kind: str,
    labels: list[str],
    unread: bool,
    internet_message_id: Optional[str],
    participants: list[dict[str, Any]],
    hygiene: HygieneResult,
    refs: list[str],
    reprocess: bool = False,
    state: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Idempotently map one service message into strict family objects."""

    state = state if state is not None else {}
    thread_by_identity = state.setdefault(
        "threads", {obj.data.get("thread_identity"): obj for obj in reader.objects(type="conversation_thread")}
    )
    participant_by_identity = state.setdefault(
        "participants", {obj.data.get("participant_identity"): obj for obj in reader.objects(type="conversation_participant")}
    )
    message_by_identity = state.setdefault(
        "messages", {obj.data.get("message_identity"): obj for obj in reader.objects(type="conversation_message")}
    )
    run_by_identity = state.setdefault(
        "runs", {obj.data.get("run_identity"): obj for obj in reader.objects(type="conversation_interpretation_run")}
    )
    mention_by_email = state.setdefault(
        "mentions",
        {
            str((obj.data.get("metadata") or {}).get("email") or "").lower(): obj
            for obj in reader.objects(type="entity_mention")
            if (obj.data.get("metadata") or {}).get("email")
        },
    )
    thread_data_by_id = state.setdefault("thread_data", {})

    thread_identity = stable_id(
        "conversation_thread", service, account_ref, provider_thread_id
    )
    thread = thread_by_identity.get(thread_identity)
    thread_created = thread is None
    if thread is None:
        thread = graph.add_object(
            "conversation_thread",
            {
                "thread_identity": thread_identity,
                "source_surface_id": source_surface_id,
                "service": service,
                "account_ref": account_ref,
                "provider_thread_id": provider_thread_id,
                "subject": subject,
                "participant_ids": [],
                "message_ids": [],
                "status": "open",
                "last_message_at": sent_at,
                "unread_count": 0,
                "message_count": 0,
                "labels": list(dict.fromkeys(labels)),
                "refs": list(dict.fromkeys(refs)),
                "metadata": {"family_projector_version": FAMILY_PROJECTOR_VERSION},
            },
        )
        thread_by_identity[thread_identity] = thread

    thread_data = thread_data_by_id.setdefault(thread.id, dict(thread.data))

    participant_ids = list(thread_data.get("participant_ids") or [])
    unique_participants: dict[str, dict[str, Any]] = {}
    for participant in participants:
        address = str(participant.get("address") or "").strip().lower()
        if not address:
            continue
        prior = unique_participants.get(address, {})
        unique_participants[address] = {
            **prior,
            **participant,
            "address": address,
            "roles": sorted(set([*(prior.get("roles") or []), *(participant.get("roles") or [])])),
        }
    for address, participant in unique_participants.items():
        identity = stable_id("conversation_participant", thread_identity, address)
        existing = participant_by_identity.get(identity)
        roles = sorted(set(participant.get("roles") or []))
        if existing is None:
            mention = mention_by_email.get(address)
            if mention is None:
                mention = graph.add_object(
                    "entity_mention",
                    {
                        "text": address,
                        "source_id": evidence_id,
                        "entity_id": None,
                        "entity_type_hint": "person",
                        "confidence": 0.95,
                        "context_snippet": address,
                        "extraction_method": "conversation_header",
                        "frame_id": None,
                        "metadata": {
                            "email": address,
                            "normalized_name": (
                                str(participant.get("display_name") or "").strip()
                                or address.split("@", 1)[0].replace(".", " ").title()
                            ),
                            "conversation_participant_identity": identity,
                        },
                    },
                )
                mention_by_email[address] = mention
            existing = graph.add_object(
                "conversation_participant",
                {
                    "participant_identity": identity,
                    "thread_id": thread.id,
                    "address": address,
                    "display_name": str(participant.get("display_name") or ""),
                    "roles": roles,
                    "entity_mention_id": mention.id,
                    "entity_id": None,
                    "refs": list(dict.fromkeys([evidence_id, *refs])),
                    "metadata": {},
                },
            )
            graph.add_relation(thread.id, existing.id, "conversation_has_participant")
            graph.add_relation(existing.id, mention.id, "conversation_participant_mention")
            participant_by_identity[identity] = existing
        else:
            merged_roles = sorted(set([*(existing.data.get("roles") or []), *roles]))
            merged_refs = list(
                dict.fromkeys([*(existing.data.get("refs") or []), evidence_id, *refs])
            )
            _patch(
                graph,
                existing.id,
                {
                    key: value for key, value in {
                        "roles": merged_roles,
                        "refs": merged_refs,
                    }.items() if existing.data.get(key) != value
                },
            )
        if existing.id not in participant_ids:
            participant_ids.append(existing.id)

    message_identity = stable_id(
        "conversation_message", service, account_ref, provider_message_id
    )
    message = message_by_identity.get(message_identity)
    message_created = message is None
    message_payload = {
        "message_identity": message_identity,
        "thread_id": thread.id,
        "source_surface_id": source_surface_id,
        "service": service,
        "account_ref": account_ref,
        "provider_message_id": provider_message_id,
        "provider_revision_ref": provider_revision_ref,
        "internet_message_id": internet_message_id,
        "sender": sender,
        "recipients": list(dict.fromkeys(recipients)),
        "cc": list(dict.fromkeys(cc)),
        "subject": subject,
        "sent_at": sent_at,
        "direction": direction,
        "message_kind": message_kind,
        "labels": list(dict.fromkeys(labels)),
        "unread": unread,
        "display_content": hygiene.display_content,
        "interpretation_content": hygiene.interpretation_content,
        "interpretation_state": hygiene.interpretation_state,
        "suppression_counts": dict(hygiene.suppression_counts),
        "injection_flags": list(hygiene.injection_flags),
        "evidence_id": evidence_id,
        "refs": list(dict.fromkeys([evidence_id, *refs])),
        "metadata": {
            "family_projector_version": FAMILY_PROJECTOR_VERSION,
            "hygiene_truncated": hygiene.truncated,
        },
    }
    prior_unread = bool(message.data.get("unread")) if message is not None else False
    if message is None:
        message = graph.add_object("conversation_message", message_payload)
        message_by_identity[message_identity] = message
        graph.add_relation(thread.id, message.id, "conversation_contains")
        graph.add_relation(message.id, evidence_id, "conversation_message_from_evidence")
    else:
        changed = {
            key: value for key, value in message_payload.items()
            if message.data.get(key) != value
        }
        _patch(graph, message.id, changed)

    message_ids = list(thread_data.get("message_ids") or [])
    if message.id not in message_ids:
        message_ids.append(message.id)
    unread_count = int(thread_data.get("unread_count") or 0)
    if message_created and unread:
        unread_count += 1
    elif not message_created and prior_unread != unread:
        unread_count += 1 if unread else -1
    thread_updates = {
        "subject": thread_data.get("subject") or subject,
        "participant_ids": participant_ids,
        "message_ids": message_ids,
        "message_count": len(message_ids),
        "unread_count": max(0, unread_count),
        "labels": sorted(set([*(thread_data.get("labels") or []), *labels])),
        "refs": list(dict.fromkeys([*(thread_data.get("refs") or []), *refs])),
    }
    if sent_at and (not thread_data.get("last_message_at") or sent_at >= thread_data["last_message_at"]):
        thread_updates["last_message_at"] = sent_at
    _patch(
        graph,
        thread.id,
        {key: value for key, value in thread_updates.items() if thread_data.get(key) != value},
    )
    thread_data.update(thread_updates)

    profile = _active_profile(reader)
    profile_data = dict(profile.data) if profile else {}
    extractor_map = dict(profile_data.get("extractor_by_facet") or {})
    # Headers and deterministic hygiene are already family materializations.
    # Body interpretation is an optional model upgrade, restricted to facets
    # explicitly routed to a model by the active profile.
    requested_facets = sorted(
        facet for facet in LLM_FACETS if facet in extractor_map
    )
    extractor_refs = sorted({
        extractor_map[facet] for facet in requested_facets if facet in extractor_map
    })
    selection_material = json.dumps(hygiene.selections, sort_keys=True, separators=(",", ":"))
    selection_id = stable_id(
        "conversation_selection", evidence_revision_id, FILTER_VERSION, selection_material
    )
    selected_hash = hashlib.sha256(
        hygiene.interpretation_content.encode("utf-8")
    ).hexdigest()
    prior_runs = [
        obj for obj in reader.objects(type="conversation_interpretation_run")
        if obj.data.get("message_id") == message.id
    ]
    run_identity = stable_id(
        "conversation_interpretation",
        message.id,
        evidence_revision_id,
        selection_id,
        profile.id if profile else "none",
        ",".join(extractor_refs),
        len(prior_runs) if reprocess else "initial",
    )
    run = run_by_identity.get(run_identity)
    if run is None:
        hygiene_state = hygiene.interpretation_state
        status = {
            "ready": "selected",
            "held": "held",
            "suppressed": "suppressed",
            "empty": "suppressed",
        }[hygiene_state]
        semantic_request = None
        policy = next(
            (
                obj for obj in reader.objects(type="connector_operational_policy")
                if obj.data.get("status") == "active"
            ),
            None,
        )
        model_limit = int(policy.data.get("max_provider_calls") or 10) if policy else 10
        model_requests = int(state.get("model_requests") or 0)
        if status == "selected" and requested_facets and model_requests < model_limit:
            semantic_request = graph.add_object(
                "selection_extraction_request",
                {
                    "request_identity": stable_id("selection_extraction_request", run_identity),
                    "evidence_id": evidence_id,
                    "revision_id": evidence_revision_id,
                    "selection_id": selection_id,
                    "selections": hygiene.selections,
                    "requested_facets": requested_facets,
                    "status": "proposed",
                    "run_ids": [],
                    "annotation_ids": [],
                    "error": None,
                    "refs": list(dict.fromkeys([evidence_id, message.id, *refs])),
                    "metadata": {"conversation_message_id": message.id},
                },
            )
            state["model_requests"] = model_requests + 1
        elif status == "selected":
            status = "deterministic_only"
        run = graph.add_object(
            "conversation_interpretation_run",
            {
                "run_identity": run_identity,
                "message_id": message.id,
                "evidence_id": evidence_id,
                "evidence_revision_id": evidence_revision_id,
                "selection_id": selection_id,
                "deterministic_filter_version": FILTER_VERSION,
                "family_projector_version": FAMILY_PROJECTOR_VERSION,
                "extraction_profile_id": profile.id if profile else None,
                "extractor_refs": extractor_refs,
                "requested_facets": requested_facets,
                "semantic_request_id": semantic_request.id if semantic_request else None,
                "selections": hygiene.selections,
                "selected_chars": len(hygiene.interpretation_content),
                "selected_content_hash": selected_hash,
                "status": status,
                "semantic_run_ids": [],
                "annotation_ids": [],
                "coverage": {
                    "selection_count": len(hygiene.selections),
                    "model_upgrade": (
                        "requested" if semantic_request else
                        "deferred_budget" if requested_facets else
                        "not_configured"
                    ),
                },
                "suppression_counts": dict(hygiene.suppression_counts),
                "reprocess_of": prior_runs[-1].id if reprocess and prior_runs else None,
                "error": None,
                "refs": list(dict.fromkeys([evidence_id, message.id, *refs])),
                "metadata": {},
            },
        )
        run_by_identity[run_identity] = run
    return {
        "thread": thread,
        "message": message,
        "interpretation_run": run,
        "thread_created": thread_created,
        "message_created": message_created,
    }


def project_conversation_native_fn(reader, source_surface_id: str) -> dict[str, Any]:
    threads = [
        obj for obj in reader.objects(type="conversation_thread")
        if obj.data.get("source_surface_id") == source_surface_id
    ]
    participants = {
        obj.id: obj for obj in reader.objects(type="conversation_participant")
    }
    mentions = {obj.id: obj for obj in reader.objects(type="entity_mention")}
    messages = {obj.id: obj for obj in reader.objects(type="conversation_message")}
    rows = []
    for thread in threads:
        participant_refs = []
        attention_refs = []
        for participant_id in thread.data.get("participant_ids") or []:
            participant = participants.get(participant_id)
            if participant is None:
                continue
            entity_ref = participant.data.get("entity_id")
            if not entity_ref and participant.data.get("entity_mention_id"):
                mention = mentions.get(participant.data["entity_mention_id"])
                entity_ref = mention.data.get("entity_id") if mention else None
            participant_refs.append(entity_ref or participant.id)
            attention_refs.append(stable_id("person_email", participant.data.get("address")))
        thread_messages = [
            messages[mid] for mid in thread.data.get("message_ids") or [] if mid in messages
        ]
        latest = max(
            thread_messages,
            key=lambda obj: (str(obj.data.get("sent_at") or ""), obj.id),
            default=None,
        )
        rows.append(
            {
                "thread_ref": thread.id,
                "title": str(thread.data.get("subject") or ""),
                "participant_refs": list(dict.fromkeys(participant_refs)),
                "attention_refs": list(dict.fromkeys(attention_refs)),
                "last_message_at": thread.data.get("last_message_at"),
                "unread_count": int(thread.data.get("unread_count") or 0),
                "message_count": int(thread.data.get("message_count") or 0),
                "latest_message_ref": latest.id if latest else None,
                "latest_sender": latest.data.get("sender") if latest else None,
                "preview": str(
                    (latest.data.get("display_content") if latest else "") or ""
                )[:240],
                "interpretation_state": str(
                    (latest.data.get("interpretation_state") if latest else "empty")
                    or "empty"
                ),
                "status": str(thread.data.get("status") or "open"),
                "refs": list(thread.data.get("refs") or [])[:20],
            }
        )
    rows.sort(
        key=lambda row: (str(row.get("last_message_at") or ""), row["thread_ref"]),
        reverse=True,
    )
    return {"threads": rows[:100], "total_count": len(rows)}


@behavior(
    name="settle_conversation_interpretation",
    on=["semantic.selection_extraction_settled"],
    view={"include_types": ["conversation_interpretation_run"]},
)
def settle_conversation_interpretation(
    event, graph, ctx, *, settings: CommunicationSettings
):
    """Mirror the generic extraction request outcome into the family run."""
    del settings
    payload = dict(event.payload or {})
    request_id = str(payload.get("request_id") or "")
    status = payload.get("status")
    if status not in {"completed", "failed"}:
        return
    run = next(
        (
            obj for obj in ctx.view.objects(type="conversation_interpretation_run")
            if obj.data.get("semantic_request_id") == request_id
        ),
        None,
    )
    if run is None:
        return
    graph.patch_object(
        run.id,
        {
            "status": "completed" if status == "completed" else "failed",
            "semantic_run_ids": list(payload.get("run_ids") or []),
            "annotation_ids": list(payload.get("annotation_ids") or []),
            "error": payload.get("error"),
        },
    )


@behavior(
    name="conversation_outbound_attention_signal",
    on=["object.created"],
    where={"object.type": "conversation_message", "object.data.direction": "outbound"},
    view={"include_types": ["conversation_thread", "conversation_participant"]},
)
def conversation_outbound_attention_signal(
    event, graph, ctx, *, settings: CommunicationSettings
):
    """A recorded reply is strong salience evidence for thread and people.

    This emits a neutral semantic signal. The attention pack owns validation
    and scoring; without that optional pack the event remains inert evidence.
    """
    del settings
    wrapper = (event.payload or {}).get("object") or {}
    data = dict(wrapper.get("data") or {})
    message_id = str(wrapper.get("id") or "")
    message_identity = str(data.get("message_identity") or message_id)
    thread_id = str(data.get("thread_id") or "")
    if not message_id or not thread_id:
        return
    counterpart_addresses = {
        str(address).strip().lower()
        for address in [*(data.get("recipients") or []), *(data.get("cc") or [])]
        if str(address).strip()
    }
    observations = [{
        "observation_id": stable_id(
            "attention_observation", "outbound_reply", message_identity, thread_id
        ),
        "subject_ref": thread_id,
        "subject_kind": "conversation_thread",
        "signal_type": "replied",
        "strength_milli": 1_000,
        "context_key": "communication",
        "source": "connector",
        "evidence_refs": [str(data.get("evidence_id") or "")],
        "metadata": {"message_id": message_id, "derivation": "outbound_message"},
    }]
    for address in sorted(counterpart_addresses):
        person_ref = stable_id("person_email", address)
        observations.append({
            "observation_id": stable_id(
                "attention_observation", "outbound_reply", message_identity, person_ref
            ),
            "subject_ref": person_ref,
            "subject_kind": "person",
            "signal_type": "replied",
            "strength_milli": 800,
            "context_key": "communication",
            "source": "connector",
            "evidence_refs": [str(data.get("evidence_id") or "")],
            "metadata": {"message_id": message_id, "derivation": "outbound_counterpart"},
        })
    _emit_event(
        graph,
        "attention.signal_observed",
        {"producer": "communication", "observations": observations},
        "communication",
    )


BEHAVIORS = [settle_conversation_interpretation]


_FOLLOWUP_CUE_RE = re.compile(
    r"\b(please|can you|could you|would you|will you|need you to|"
    r"action item|to[- ]?do|follow up|send me|let me know)\b",
    re.IGNORECASE,
)


@behavior(
    name="project_conversation_followup_candidate",
    on=["object.created"],
    where={"object.type": "semantic_annotation", "object.data.facet": "relation_mention", "object.data.status": "active"},
    view={"include_types": ["semantic_annotation", "activity_evidence", "conversation_message", "task_candidate"]},
    creates=["task_candidate"],
)
def project_conversation_followup_candidate(event, graph, ctx, *, settings: CommunicationSettings):
    """Project one reviewable task only from an explicit exact-span request.

    Relation extraction alone is not actionability. The original evidence span
    must contain a direct request cue, belong to a clean inbound conversation
    message, and retain the extractor identity. The result stays a candidate;
    no email body can activate work by itself.
    """
    wrapper = event.payload.get("object") or {}
    data = wrapper.get("data") or {}
    body = data.get("body") or {}
    exact = str((data.get("selector") or {}).get("exact") or body.get("text") or "").strip()
    if not exact or not _FOLLOWUP_CUE_RE.search(exact):
        return
    evidence_id = str(data.get("evidence_id") or "")
    messages = [
        obj for obj in ctx.view.objects(type="conversation_message")
        if obj.data.get("evidence_id") == evidence_id
    ]
    if not messages:
        return
    message = messages[-1]
    if (
        message.data.get("direction") != "inbound"
        or message.data.get("interpretation_state") != "ready"
        or message.data.get("injection_flags")
    ):
        return
    annotation_id = str(wrapper.get("id") or "")
    if any(
        (obj.data.get("metadata") or {}).get("annotation_id") == annotation_id
        for obj in ctx.view.objects(type="task_candidate")
    ):
        return
    evidence = graph.get_object(evidence_id)
    if evidence is None:
        return
    title = exact[:120].rstrip()
    candidate = graph.add_object("task_candidate", {
        "candidate_identity": stable_id("task_candidate", annotation_id),
        "text": exact, "confidence": float(data.get("confidence") or 0.5),
        "evidence_id": evidence_id,
        "evidence_identity": data.get("evidence_identity"),
        "revision_id": data.get("revision_id"),
        "extraction_record_id": data.get("run_id"),
        "extractor_id": data.get("extractor_id"),
        "extractor_version": data.get("extractor_version"),
        "extraction_config_id": data.get("config_hash"),
        "status": "candidate", "invalidation_reason": None,
        "metadata": {
            "projector": "communication.followup",
            "annotation_id": annotation_id,
            "conversation_message_id": message.id,
            "owner_ref": "owner",
            "requires_review": True,
        },
        "title": title, "description": exact,
    })
    graph.add_relation(candidate.id, evidence_id, "extracted_from")
    graph.add_relation(candidate.id, annotation_id, "projected_from_annotation")


BEHAVIORS = [
    settle_conversation_interpretation,
    conversation_outbound_attention_signal,
    project_conversation_followup_candidate,
]


__all__ = [
    "FAMILY_PROJECTOR_VERSION",
    "LLM_FACETS",
    "BEHAVIORS",
    "materialize_conversation_message_fn",
    "project_conversation_native_fn",
    "stable_id",
]
