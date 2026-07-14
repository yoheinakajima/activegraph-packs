"""The synthesis pass: prepare (reads) → perform (provider) → commit (writes).

Every proposal cites refs from the prepared input or is dropped at commit
(ADR 0043). Identity proposals become ``profile_candidate``s anchored to
owner-scoped evidence and join the existing review/verdict flow; project
proposals become ``project_candidate``s under the projects pack's
idempotence rules. The run receipt records inputs, proposals, and the
noise synthesis deliberately set aside.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from .settings import SubjectSynthesisSettings

SYNTHESIS_PROJECTOR = "subject_synthesis.profile"
SYNTHESIS_VERSION = "0.1.0"


def _stable(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()[:32]}"


def _norm(name: str) -> str:
    return " ".join(str(name).split()).casefold()


def request_subject_synthesis_fn(
    graph, *, reason: str = "", subject_ref: str = "owner", reader=None
) -> dict[str, Any]:
    """Idempotent while one request is open: repeated triggers coalesce.

    ``reader`` lets view-gated callers (behaviors) pass their read view
    while writes go through their write handle.
    """
    view = reader or graph
    existing = next(
        (
            obj for obj in view.objects(type="subject_synthesis_request")
            if (obj.data or {}).get("status") == "proposed"
            and (obj.data or {}).get("subject_ref") == subject_ref
        ),
        None,
    )
    if existing is not None:
        return {"ok": True, "request_id": existing.id, "created": False}
    prior = len(list(view.objects(type="subject_synthesis_request")))
    request = graph.add_object("subject_synthesis_request", {
        "request_identity": _stable("synthesis_request", subject_ref, prior),
        "subject_ref": subject_ref,
        "reason": str(reason or "")[:200],
        "status": "proposed",
        "run_id": None,
        "error": None,
        "metadata": {},
    })
    return {"ok": True, "request_id": request.id, "created": True}


def pending_subject_synthesis_requests_fn(reader) -> list[str]:
    return [
        obj.id for obj in reader.objects(type="subject_synthesis_request")
        if (obj.data or {}).get("status") == "proposed"
    ]


def prepare_subject_synthesis_fn(
    graph, request_id: str, *, settings: Optional[SubjectSynthesisSettings] = None,
    reader=None,
) -> dict[str, Any]:
    """Phase 1 — graph reads only. Rows carry their graph ref so the model
    can cite exactly what it reasons from."""
    from packs.subject_profile.projection import classify_subject_attribute

    settings = settings or SubjectSynthesisSettings()
    view = reader or graph
    request = graph.get_object(request_id)
    if request is None or (request.data or {}).get("status") != "proposed":
        return {"status": "skipped", "request_id": request_id}
    subject_ref = str((request.data or {}).get("subject_ref") or "owner")

    facts: list[dict[str, Any]] = []
    for obj in view.objects(type="subject_fact"):
        data = obj.data or {}
        if data.get("subject_ref") != subject_ref or data.get("status") != "promoted":
            continue
        facts.append({
            "ref": obj.id,
            "attribute": str(data.get("attribute") or ""),
            "value": str(data.get("value") or "")[:400],
            "class": classify_subject_attribute(str(data.get("attribute") or "")),
            "evidence_id": data.get("evidence_id"),
        })
        if len(facts) >= settings.max_input_facts:
            break

    labels: list[dict[str, str]] = []
    for profile in view.objects(type="integration_profile"):
        if (profile.data or {}).get("status") != "active":
            continue
        for container in ((profile.data or {}).get("data_topology") or {}).get("containers") or []:
            if str(container.get("type") or "") != "user":
                continue
            name = str(container.get("name") or "").strip()
            if name:
                labels.append({"ref": profile.id, "name": name})
            if len(labels) >= settings.max_input_labels:
                break

    mentions: dict[str, int] = {}
    for mention in view.objects(type="entity_mention"):
        entity_id = (mention.data or {}).get("entity_id")
        if entity_id:
            mentions[str(entity_id)] = mentions.get(str(entity_id), 0) + 1
    entities: list[dict[str, Any]] = []
    for entity in view.objects(type="entity"):
        count = mentions.get(entity.id, 0)
        if count < 2:
            continue
        entities.append({
            "ref": entity.id,
            "name": str((entity.data or {}).get("name") or "")[:120],
            "type": str((entity.data or {}).get("entity_type") or ""),
            "mentions": count,
        })
    entities.sort(key=lambda row: -row["mentions"])
    entities = entities[: settings.max_input_entities]

    existing_projects = [
        str((obj.data or {}).get("name") or "")
        for obj in view.objects(type="project")
        if (obj.data or {}).get("status") == "active"
    ]
    candidate_status = {
        _norm((obj.data or {}).get("name") or ""): str((obj.data or {}).get("status"))
        for obj in view.objects(type="project_candidate")
    }
    dismissed = sorted(
        name for name, status in candidate_status.items() if status == "dismissed"
    )

    return {
        "status": "prepared",
        "request_id": request_id,
        "subject_ref": subject_ref,
        "facts": facts,
        "labels": labels,
        "entities": entities,
        "existing_projects": existing_projects,
        "dismissed_projects": dismissed,
        "identity_attributes": list(settings.identity_attributes),
        "max_identity": settings.max_identity_candidates,
        "max_projects": settings.max_project_candidates,
    }


def _synthesis_prompt(payload: dict[str, Any]) -> tuple[str, str]:
    system = (
        "You organize what is already known about a person into structure. "
        "Distinguish who the person IS and what their world is made of from "
        "how they use their tools: mail labels and folders are usage "
        "patterns, not the map — at most corroboration. Propose only what "
        "the given material supports, cite refs for every proposal, and "
        "respond with STRICT JSON only, no prose."
    )
    instructions = {
        "identity": (
            "structured attributes lifted from the material; attribute must "
            f"be one of {payload['identity_attributes']}; max "
            f"{payload['max_identity']}; never repeat an attribute+value "
            "already present in facts"
        ),
        "projects": (
            "the person's real threads of work/life; max "
            f"{payload['max_projects']}; never propose anything in "
            "existing_projects or dismissed_projects; prefer few and real "
            "over many and plausible"
        ),
        "noise": "material you deliberately set aside (e.g. tool-usage labels), with reasons",
    }
    user = json.dumps({
        "material": {
            "facts": payload["facts"],
            "mail_labels": payload["labels"],
            "recurring_entities": payload["entities"],
        },
        "existing_projects": payload["existing_projects"],
        "dismissed_projects": payload["dismissed_projects"],
        "respond_with": {
            "identity": [{"attribute": "…", "value": "…", "refs": ["ref"], "rationale": "…"}],
            "projects": [{"name": "…", "refs": ["ref"], "rationale": "…"}],
            "noise": [{"name": "…", "reason": "…"}],
        },
        "rules": instructions,
    }, ensure_ascii=False)
    return system, user


def perform_subject_synthesis(
    payload: dict[str, Any],
    *,
    provider=None,
    model: Optional[str] = None,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """Phase 2 — provider only, zero graph access."""
    from packs.llm_provider import (
        configured_llm_provider,
        default_model_for,
        get_llm_provider,
        parse_json_payload,
        response_text,
    )

    resolved = configured_llm_provider()
    if provider is None:
        if not resolved.configured:
            return {"identity": [], "projects": [], "noise": [],
                    "model": None, "error": "synthesis_provider_unavailable"}
        provider = get_llm_provider()
    model = model or default_model_for(resolved) or ""
    from activegraph.llm import LLMMessage

    system, user = _synthesis_prompt(payload)
    try:
        response = provider.complete(
            system=system,
            messages=[LLMMessage(role="user", content=user)],
            model=model,
            max_tokens=2_000,
            temperature=0.0,
            top_p=1.0,
            output_schema=None,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        return {"identity": [], "projects": [], "noise": [], "model": model,
                "error": f"{type(exc).__name__}: {exc}"[:300]}
    text = response_text(response)
    parsed = parse_json_payload(text) or {}
    return {
        "identity": list(parsed.get("identity") or []),
        "projects": list(parsed.get("projects") or []),
        "noise": list(parsed.get("noise") or []),
        "model": model,
        "response_sample": text[:400],
        "error": None if parsed else "synthesis_response_unparseable",
    }


def commit_subject_synthesis_fn(
    graph, request_id: str, payload: dict[str, Any], outcome: dict[str, Any],
    *, settings: Optional[SubjectSynthesisSettings] = None, reader=None,
) -> dict[str, Any]:
    """Phase 3 — writes only. Uncited or unknown-ref proposals are dropped;
    everything minted stays a candidate awaiting the owner's verdict."""
    settings = settings or SubjectSynthesisSettings()
    view = reader or graph
    request = graph.get_object(request_id)
    if request is None or (request.data or {}).get("status") != "proposed":
        return {"ok": False, "reason": "request_not_open", "request_id": request_id}
    subject_ref = str(payload.get("subject_ref") or "owner")

    valid_refs = {row["ref"] for row in payload.get("facts") or []}
    valid_refs.update(row["ref"] for row in payload.get("labels") or [])
    valid_refs.update(row["ref"] for row in payload.get("entities") or [])
    fact_rows = {row["ref"]: row for row in payload.get("facts") or []}

    promoted_values = {
        (row["attribute"], row["value"]) for row in payload.get("facts") or []
    }
    existing_candidates = {
        str((obj.data or {}).get("candidate_identity") or "")
        for obj in view.objects(type="profile_candidate")
    }

    identity_minted = 0
    dropped_uncited = 0
    allowed = set(settings.identity_attributes)
    for row in (outcome.get("identity") or [])[: settings.max_identity_candidates]:
        attribute = str((row or {}).get("attribute") or "").strip().lower()
        value = str((row or {}).get("value") or "").strip()
        refs = [str(ref) for ref in (row or {}).get("refs") or [] if str(ref) in valid_refs]
        if not attribute or not value or attribute not in allowed:
            continue
        if not refs:
            dropped_uncited += 1
            continue
        if (attribute, value) in promoted_values:
            continue
        # Identity claims must trace to owner-attested material: the anchor
        # is the first cited fact's evidence (subject-scope gate downstream).
        anchor = next(
            (fact_rows[ref] for ref in refs if ref in fact_rows
             and fact_rows[ref].get("evidence_id")),
            None,
        )
        if anchor is None:
            dropped_uncited += 1
            continue
        evidence = graph.get_object(str(anchor["evidence_id"]))
        if evidence is None:
            dropped_uncited += 1
            continue
        candidate_identity = _stable("synthesis_candidate", subject_ref, attribute, value)
        if candidate_identity in existing_candidates:
            continue
        graph.add_object("profile_candidate", {
            "candidate_identity": candidate_identity,
            "text": str((row or {}).get("rationale") or f"{attribute}: {value}")[:500],
            "confidence": 0.7,
            "evidence_id": evidence.id,
            "evidence_identity": str((evidence.data or {}).get("evidence_identity") or ""),
            "revision_id": str((evidence.data or {}).get("revision_id") or ""),
            "extraction_record_id": request_id,
            "extractor_id": SYNTHESIS_PROJECTOR,
            "extractor_version": SYNTHESIS_VERSION,
            "extraction_config_id": f"synthesis@{SYNTHESIS_VERSION}",
            "status": "candidate",
            "invalidation_reason": None,
            "metadata": {
                "projector": SYNTHESIS_PROJECTOR,
                "synthesis_refs": refs,
            },
            "attribute": attribute,
            "value": value,
        })
        identity_minted += 1

    project_minted = 0
    candidate_by_name = {
        _norm((obj.data or {}).get("name") or ""): obj
        for obj in view.objects(type="project_candidate")
    }
    confirmed_names = {
        _norm((obj.data or {}).get("name") or "")
        for obj in view.objects(type="project")
        if (obj.data or {}).get("status") == "active"
    }
    for row in (outcome.get("projects") or [])[: settings.max_project_candidates]:
        name = " ".join(str((row or {}).get("name") or "").split())
        refs = [str(ref) for ref in (row or {}).get("refs") or [] if str(ref) in valid_refs]
        rationale = str((row or {}).get("rationale") or "").strip()
        key = _norm(name)
        if not key or len(key) < 2:
            continue
        if not refs:
            dropped_uncited += 1
            continue
        if key in confirmed_names:
            continue
        existing = candidate_by_name.get(key)
        if existing is not None:
            if (existing.data or {}).get("status") in {"confirmed", "dismissed"}:
                continue
            merged_sources = list(dict.fromkeys(
                [*((existing.data or {}).get("sources") or []), *refs]
            ))
            graph.patch_object(existing.id, {
                "kind": "synthesized",
                "score_milli": max(800, int((existing.data or {}).get("score_milli") or 0)),
                "sources": merged_sources,
                "rationale": rationale or (existing.data or {}).get("rationale"),
            }, rationale="synthesis refreshed the proposal")
            project_minted += 1
            continue
        graph.add_object("project_candidate", {
            "candidate_identity": _stable("synthesis_project", subject_ref, key),
            "name": name,
            "kind": "synthesized",
            "score_milli": 800,
            "sources": refs,
            "rationale": rationale or "proposed by synthesis over your confirmed material",
            "status": "proposed",
            "project_id": None,
            "metadata": {"projector": SYNTHESIS_PROJECTOR},
        })
        project_minted += 1

    error = outcome.get("error")
    status = "failed" if error and not (identity_minted or project_minted) else "completed"
    run = graph.add_object("subject_synthesis_run", {
        "run_identity": _stable(
            "synthesis_run", request_id,
            str((request.data or {}).get("request_identity") or ""),
        ),
        "subject_ref": subject_ref,
        "status": status,
        "model": outcome.get("model"),
        "inputs": {
            "facts": len(payload.get("facts") or []),
            "labels": len(payload.get("labels") or []),
            "entities": len(payload.get("entities") or []),
        },
        "proposed": {
            "identity_candidates": identity_minted,
            "project_candidates": project_minted,
            "dropped_uncited": dropped_uncited,
        },
        "noise": [
            {"name": str((row or {}).get("name") or "")[:120],
             "reason": str((row or {}).get("reason") or "")[:200]}
            for row in (outcome.get("noise") or [])[:16]
        ],
        "error": error,
        "metadata": {
            "response_sample": str(outcome.get("response_sample") or "")[:400],
        },
    })
    graph.patch_object(request_id, {
        "status": "failed" if status == "failed" else "completed",
        "run_id": run.id,
        "error": error,
    }, rationale="synthesis settled")
    return {
        "ok": status == "completed",
        "run_id": run.id,
        "identity_candidates": identity_minted,
        "project_candidates": project_minted,
        "dropped_uncited": dropped_uncited,
        "error": error,
    }


def run_subject_synthesis_fn(
    graph, request_id: str, *,
    settings: Optional[SubjectSynthesisSettings] = None,
    provider=None, reader=None,
) -> dict[str, Any]:
    """Synchronous composition — the pack default (D061); hosts with a pump
    run the three phases themselves."""
    settings = settings or SubjectSynthesisSettings()
    payload = prepare_subject_synthesis_fn(
        graph, request_id, settings=settings, reader=reader
    )
    if payload.get("status") != "prepared":
        return {"ok": False, "reason": payload.get("status"), "request_id": request_id}
    outcome = perform_subject_synthesis(
        payload, provider=provider, model=settings.model,
        timeout_seconds=settings.timeout_seconds,
    )
    return commit_subject_synthesis_fn(
        graph, request_id, payload, outcome, settings=settings, reader=reader
    )


__all__ = [
    "SYNTHESIS_PROJECTOR",
    "SYNTHESIS_VERSION",
    "commit_subject_synthesis_fn",
    "pending_subject_synthesis_requests_fn",
    "perform_subject_synthesis",
    "prepare_subject_synthesis_fn",
    "request_subject_synthesis_fn",
    "run_subject_synthesis_fn",
]
