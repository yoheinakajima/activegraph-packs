"""The setup draft: one versioned, editable proposal of the owner's world.

ADR 0046 / D068. The strong cross-source pass reads only bounded,
provenance-bearing inputs (promoted facts, research findings, comprehension
summaries, connector signal maps — never raw pages or full mailboxes, with
deterministic packing and recorded included refs) and proposes ONE durable
draft whose items route to their canonical owners:

    identity            -> subject_profile candidates
    narrative           -> narrative-class subject candidates
    instructions        -> instruction-class subject candidates
    projects            -> projects candidates
    people              -> entity/relationship candidates (owner-declaration
                           promotion at submit — connector evidence alone can
                           never mint an owner fact, ADR 0036)
    access              -> information_access_hint objects (never memory or
                           identity)

No uncited item may commit. Accept/reject is a verdict on the item's
pre-recorded prediction; an owner edit supersedes the proposal and never
counts as a correct system prediction. Submission promotes through existing
pipelines with restartable partial failure; a new draft version supersedes an
unsubmitted head and never mutates a submitted one. Zero-key stores compose a
smaller deterministic draft through the identical review path.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from .settings import SubjectSynthesisSettings


DRAFT_COMPOSER = "subject_synthesis.draft"
DRAFT_VERSION = "0.1.0"

DRAFT_SECTIONS = ("identity", "narrative", "instructions", "projects", "people", "access")

SECTION_DESTINATIONS = {
    "identity": "subject_profile",
    "narrative": "subject_profile",
    "instructions": "instructions",
    "projects": "projects",
    "people": "entity_relationship",
    "access": "access_hint",
}

ITEM_VERDICTS = ("accept", "reject", "edit", "defer")

#: Sections whose acceptance promotes through the owner-declaration path
#: (owner-scoped evidence minted at submit): the cited material is connector
#: or research content, which can never mint an owner fact by itself.
_DECLARATION_SECTIONS = ("narrative", "instructions", "people")

_SECTION_ATTRIBUTES = {
    "narrative": "profile_statement",
    "instructions": "instruction",
    "people": "person",
}


def _stable(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()}"


def _estimate_tokens(value: Any) -> int:
    return max(1, len(json.dumps(value, ensure_ascii=False, default=str)) // 4)


def _draft_by_ref(reader, draft_ref: str):
    getter = getattr(reader, "get_object", None)
    if callable(getter):
        try:
            obj = getter(draft_ref)
        except Exception:
            obj = None
        if obj is not None and getattr(obj, "type", None) == "setup_draft":
            return obj
    return next(
        (obj for obj in reader.objects(type="setup_draft")
         if obj.data.get("draft_identity") == draft_ref),
        None,
    )


def current_setup_draft_fn(reader, *, subject_ref: str = "owner"):
    """The head (highest, non-superseded) draft version, or None."""
    rows = [
        obj for obj in reader.objects(type="setup_draft")
        if obj.data.get("subject_ref") == subject_ref
        and obj.data.get("status") != "superseded"
    ]
    if not rows:
        return None
    rows.sort(key=lambda obj: int(obj.data.get("version") or 0))
    return rows[-1]


def _items_for_draft(reader, draft_id: str):
    rows = [
        obj for obj in reader.objects(type="setup_draft_item")
        if obj.data.get("draft_id") == draft_id
    ]
    rows.sort(key=lambda obj: (
        DRAFT_SECTIONS.index(obj.data.get("section"))
        if obj.data.get("section") in DRAFT_SECTIONS else 99,
        obj.data.get("item_identity") or "",
    ))
    return rows


def _predict_item_verdict(reader, destination: str) -> dict[str, Any]:
    """Deterministic Laplace-smoothed prediction from prior item verdicts in
    the same destination scope — recorded before the owner answers, never
    backfilled (ADR 0018). Owner edits count as their own verdict class, so
    they can never inflate accept-accuracy."""
    counts = {verdict: 0 for verdict in ITEM_VERDICTS}
    for obj in reader.objects(type="setup_draft_item"):
        if obj.data.get("destination") != destination:
            continue
        verdict = obj.data.get("verdict")
        if verdict in counts:
            counts[verdict] += 1
    total = sum(counts.values())
    predicted = max(ITEM_VERDICTS, key=lambda v: counts[v]) if total else "accept"
    confidence = round(100 * (counts[predicted] + 1) / (total + len(ITEM_VERDICTS)))
    return {
        "predicted_verdict": predicted,
        "predicted_confidence_percent": int(confidence),
        "prediction_basis": {
            "scope": f"setup_draft:{destination}",
            "prior_verdicts": counts,
            "prior_total": total,
        },
    }


# ---- the strong pass --------------------------------------------------------

def request_setup_draft_fn(
    graph, *, subject_ref: str = "owner", reason: str = "", reader=None
) -> dict[str, Any]:
    """Request the cross-source draft pass; hosts settle it on their pump.
    Idempotent while one draft request is open for the subject."""
    view = reader or graph
    for obj in view.objects(type="subject_synthesis_request"):
        data = obj.data or {}
        if (
            data.get("subject_ref") == subject_ref
            and data.get("status") == "proposed"
            and (data.get("metadata") or {}).get("kind") == "setup_draft"
        ):
            return {"ok": True, "request_id": obj.id, "already_open": True}
    request = graph.add_object("subject_synthesis_request", {
        "request_identity": _stable("setup_draft_request", subject_ref, reason),
        "subject_ref": subject_ref,
        "reason": reason or "compose the setup draft",
        "status": "proposed",
        "metadata": {"kind": "setup_draft"},
    })
    return {"ok": True, "request_id": request.id, "created": True}


def prepare_setup_draft_fn(
    graph, request_id: str, *,
    settings: Optional[SubjectSynthesisSettings] = None, reader=None,
) -> dict[str, Any]:
    """Phase 1 — deterministic packing of every allowed input source (ADR
    0046 §D1) under per-source budgets, with included refs recorded. Raw
    pages and raw messages never enter this payload."""
    from .comprehension import comprehension_inputs_for_synthesis_fn
    from .engine import prepare_subject_synthesis_fn

    settings = settings or SubjectSynthesisSettings()
    base = prepare_subject_synthesis_fn(
        graph, request_id, settings=settings, reader=reader
    )
    if base.get("status") != "prepared":
        return base
    view = reader or graph

    research: list[dict[str, Any]] = []
    research_coverage: list[dict[str, Any]] = []
    for run in view.objects(type="web_research_run"):
        data = run.data or {}
        if data.get("status") not in ("completed", "partial"):
            continue
        research_coverage.append({
            "run_ref": run.id,
            "status": data.get("status"),
            "stop_reason": data.get("stop_reason"),
            "rounds": data.get("rounds_executed"),
            "findings": len(data.get("findings") or []),
        })
        for finding in data.get("findings") or []:
            if finding.get("injection_flags"):
                continue  # flagged content stays evidence, never derivation
            research.append({
                "ref": run.id,
                "claim": str(finding.get("claim") or "")[:300],
                "url": str(finding.get("url") or "")[:200],
            })

    comprehension = comprehension_inputs_for_synthesis_fn(view)

    signal_maps: list[dict[str, Any]] = []
    for profile in view.objects(type="integration_profile"):
        data = profile.data or {}
        if data.get("status") != "active":
            continue
        surfaces = [
            {
                "surface": str(row.get("surface") or ""),
                "candidate_types": list(row.get("candidate_types") or []),
            }
            for row in (data.get("signal_map") or [])[:8]
            if row.get("candidate_types")
        ]
        signal_maps.append({
            "ref": profile.id,
            "service": str(data.get("service") or ""),
            "account_ref": str(data.get("account_ref") or ""),
            "surfaces": surfaces,
        })

    project_candidates = [
        {
            "ref": obj.id,
            "name": str(obj.data.get("name") or "")[:120],
            "status": obj.data.get("status"),
            "kind": obj.data.get("kind"),
            "rationale": str(obj.data.get("rationale") or "")[:200],
        }
        for obj in view.objects(type="project_candidate")
        if obj.data.get("status") == "proposed"
    ][:16]

    # Deterministic packing: per-source budgets, drop whole tail rows (never
    # mid-row truncation), and record what was included vs dropped.
    budgets = {
        "facts": 3_000, "research": 2_500, "comprehension": 6_000,
        "signal_maps": 800, "project_candidates": 800, "entities": 600,
    }
    packing: dict[str, dict[str, int]] = {}

    def _pack(name: str, rows: list) -> list:
        budget = budgets[name]
        kept, spent = [], 0
        for row in rows:
            cost = _estimate_tokens(row)
            if spent + cost > budget:
                break
            kept.append(row)
            spent += cost
        packing[name] = {"included": len(kept), "dropped": len(rows) - len(kept),
                         "tokens": spent, "budget": budget}
        return kept

    facts = _pack("facts", base.get("facts") or [])
    research = _pack("research", research)
    comprehension_rows = _pack(
        "comprehension",
        (comprehension.get("aggregates") or []) + (comprehension.get("leaves") or []),
    )
    signal_maps = _pack("signal_maps", signal_maps)
    project_candidates = _pack("project_candidates", project_candidates)
    entities = _pack("entities", base.get("entities") or [])

    included_refs = sorted({
        str(row.get("ref"))
        for source in (facts, research, comprehension_rows, signal_maps,
                       project_candidates, entities)
        for row in source
        if row.get("ref")
    })

    return {
        **base,
        "kind": "setup_draft",
        "facts": facts,
        "entities": entities,
        "research": research,
        "research_coverage": research_coverage,
        "comprehension": comprehension_rows,
        "comprehension_coverage": comprehension.get("coverage") or [],
        "signal_maps": signal_maps,
        "project_candidates": project_candidates,
        "packing": packing,
        "included_refs": included_refs,
        "max_items_per_section": 8,
    }


def _draft_prompt(payload: dict[str, Any]) -> tuple[str, str]:
    system = (
        "You compose a setup draft: the reviewed starting understanding of a "
        "person and their world, assembled ONLY from the provided material. "
        "Every item cites the refs it reasons from; uncited items are "
        "discarded. Instructions about how an assistant should behave are "
        "never identity. Sent-mail summaries show what the person actually "
        "works on; research findings show their public presence. Respond "
        "with STRICT JSON only, no prose."
    )
    cap = payload.get("max_items_per_section") or 8
    shape = {
        "identity": [{"attribute": f"one of {payload['identity_attributes']}",
                      "value": "…", "refs": ["…"], "rationale": "…",
                      "confidence": 0.7, "uncertainty": ""}],
        "narrative": [{"statement": "first-person-adjacent description in the person's own register",
                       "refs": ["…"], "rationale": "…"}],
        "instructions": [{"instruction": "how an assistant should work with this person",
                          "refs": ["…"], "rationale": "…"}],
        "projects": [{"name": "…", "description": "2-3 evidence-backed sentences",
                      "status_note": "", "people": ["…"], "refs": ["…"],
                      "rationale": "…"}],
        "people": [{"name": "…", "relationship": "evidence-based context",
                    "refs": ["…"], "rationale": "…", "uncertainty": ""}],
        "access": [{"question_class": "what future questions this serves",
                    "source": "which connected source/surface",
                    "strategy": "label/folder/query pattern or capability",
                    "refs": ["…"], "rationale": "…"}],
    }
    user = (
        f"Return at most {cap} items per section as JSON with this exact "
        f"shape:\n{json.dumps(shape, ensure_ascii=False)}\n\n"
        f"PROMOTED FACTS (already confirmed; do not re-propose):\n"
        f"{json.dumps(payload.get('facts') or [], ensure_ascii=False)}\n\n"
        f"RESEARCH FINDINGS:\n{json.dumps(payload.get('research') or [], ensure_ascii=False)}\n\n"
        f"SENT-MAIL SUMMARIES:\n{json.dumps(payload.get('comprehension') or [], ensure_ascii=False)}\n\n"
        f"CONNECTED SOURCES:\n{json.dumps(payload.get('signal_maps') or [], ensure_ascii=False)}\n\n"
        f"EXISTING PROJECT CANDIDATES (refine, don't duplicate):\n"
        f"{json.dumps(payload.get('project_candidates') or [], ensure_ascii=False)}\n\n"
        f"RECURRING ENTITIES:\n{json.dumps(payload.get('entities') or [], ensure_ascii=False)}\n\n"
        f"CONFIRMED PROJECTS (never re-propose): {payload.get('existing_projects')}\n"
        f"DISMISSED (never resurrect): {payload.get('dismissed_projects')}"
    )
    return system, user


def perform_setup_draft(
    payload: dict[str, Any], *, provider=None, model: Optional[str] = None,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    """Phase 2 — the reasoning-model pass, zero graph access."""
    from packs.llm_provider import (
        configured_llm_provider, get_llm_provider, parse_json_payload,
        resolve_model_for_role,
    )

    resolved = configured_llm_provider()
    if provider is None:
        if not resolved.configured:
            return {"ok": False, "error": "draft_provider_unavailable",
                    "model": None, "sections": {}}
        provider = get_llm_provider()
    model = model or resolve_model_for_role("comprehension_reasoning", resolved)
    from activegraph.llm import LLMMessage

    system, user = _draft_prompt(payload)
    try:
        response = provider.complete(
            system=system,
            messages=[LLMMessage(role="user", content=user)],
            model=model or "",
            max_tokens=4_000,
            temperature=0.0,
            top_p=1.0,
            output_schema=None,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300],
                "model": model, "sections": {}}
    text = getattr(response, "text", "") or ""
    parsed = parse_json_payload(text) or {}
    return {
        "ok": True,
        "model": model,
        "sections": {key: list(parsed.get(key) or []) for key in DRAFT_SECTIONS},
        "response_sample": text[:400],
        "response_length": len(text),
        "error": None,
    }


def _sanitize(value: str, limit: int = 500) -> tuple[str, list[str]]:
    from packs.tool_gateway.sanitizer import sanitize_output
    from packs.tool_gateway.untrusted import scan_for_injection

    cleaned, _ = sanitize_output(str(value)[:limit])
    return cleaned, scan_for_injection(cleaned)


def _mint_draft(graph, *, subject_ref: str, source: str, run_id: Optional[str],
                included_refs: list[str], coverage: dict[str, Any]) -> Any:
    head = current_setup_draft_fn(graph, subject_ref=subject_ref)
    version = 1
    supersedes = None
    if head is not None:
        version = int(head.data.get("version") or 0) + 1
        if head.data.get("status") in ("proposed", "deferred"):
            supersedes = head.id
    draft = graph.add_object("setup_draft", {
        "draft_identity": _stable("setup_draft", subject_ref, version),
        "version": version,
        "subject_ref": subject_ref,
        "status": "proposed",
        "source": source,
        "run_id": run_id,
        "supersedes": str(head.data.get("draft_identity")) if supersedes else None,
        "included_refs": included_refs[:200],
        "coverage": coverage,
        "counts": {},
        "metadata": {"composer": f"{DRAFT_COMPOSER}@{DRAFT_VERSION}"},
    })
    if supersedes is not None:
        # An unsubmitted head is replaced; a submitted head stays immutable
        # history and is never touched (ADR 0046 §1).
        graph.patch_object(supersedes, {"status": "superseded"},
                           rationale="a newer draft version supersedes it")
    return draft


def _mint_item(
    graph, view, *, draft, section: str, proposed: dict[str, Any],
    rationale: str, evidence_refs: list[str], confidence: float,
    uncertainty: str, candidate_ref: Optional[str], flags: list[str],
) -> Any:
    destination = SECTION_DESTINATIONS[section]
    prediction = _predict_item_verdict(view, destination)
    return graph.add_object("setup_draft_item", {
        "item_identity": _stable(
            "draft_item", draft.id, section,
            json.dumps(proposed, sort_keys=True, ensure_ascii=False),
        ),
        "draft_id": draft.id,
        "section": section,
        "destination": destination,
        "proposed": proposed,
        "rationale": rationale[:500],
        "evidence_refs": evidence_refs[:10],
        "confidence": min(1.0, max(0.0, confidence)),
        "uncertainty": uncertainty[:300],
        "status": "proposed",
        "candidate_ref": candidate_ref,
        "injection_flags": sorted(set(flags)),
        **prediction,
        "metadata": {},
    })


def commit_setup_draft_fn(
    graph, request_id: str, payload: dict[str, Any], outcome: dict[str, Any],
    *, settings: Optional[SubjectSynthesisSettings] = None, reader=None,
) -> dict[str, Any]:
    """Phase 3 — mint the draft, its items, the underlying candidates for
    identity/projects, and the run receipt. Uncited items are dropped and
    counted; nothing here writes canonical state."""
    from .engine import SYNTHESIS_PROJECTOR, SYNTHESIS_VERSION

    settings = settings or SubjectSynthesisSettings()
    view = reader or graph
    request = graph.get_object(request_id)
    if request is None or (request.data or {}).get("status") != "proposed":
        return {"ok": False, "reason": "request_not_open", "request_id": request_id}
    subject_ref = str(payload.get("subject_ref") or "owner")

    valid_refs = set(payload.get("included_refs") or [])
    fact_rows = {row["ref"]: row for row in payload.get("facts") or []}
    sections = dict(outcome.get("sections") or {})
    cap = int(payload.get("max_items_per_section") or 8)
    error = outcome.get("error")

    if error and not any(sections.values()):
        graph.patch_object(request_id, {
            "status": "failed", "error": str(error)[:300],
        }, rationale="setup draft pass failed")
        return {"ok": False, "reason": "perform_failed", "error": error}

    draft = _mint_draft(
        graph, subject_ref=subject_ref, source="synthesis", run_id=None,
        included_refs=list(payload.get("included_refs") or []),
        coverage={
            "packing": payload.get("packing") or {},
            "research": payload.get("research_coverage") or [],
            "comprehension": payload.get("comprehension_coverage") or [],
        },
    )

    counts = {section: 0 for section in DRAFT_SECTIONS}
    dropped_uncited = 0

    def _refs_of(row: dict[str, Any]) -> list[str]:
        return [str(ref) for ref in (row.get("refs") or []) if str(ref) in valid_refs]

    promoted_values = {
        (row["attribute"], row["value"]) for row in payload.get("facts") or []
    }
    allowed_attrs = set(settings.identity_attributes)
    for row in (sections.get("identity") or [])[:cap]:
        attribute = str((row or {}).get("attribute") or "").strip().lower()
        value, value_flags = _sanitize((row or {}).get("value") or "", 300)
        refs = _refs_of(row or {})
        if not attribute or not value or attribute not in allowed_attrs:
            continue
        if not refs:
            dropped_uncited += 1
            continue
        if (attribute, value) in promoted_values:
            continue
        # Identity still requires an owner-attested anchor (ADR 0036): a
        # cited promoted fact whose evidence carries the owner scope.
        anchor = next(
            (fact_rows[ref] for ref in refs
             if ref in fact_rows and fact_rows[ref].get("evidence_id")),
            None,
        )
        candidate_ref = None
        if anchor is not None:
            evidence = graph.get_object(str(anchor["evidence_id"]))
            if evidence is not None:
                candidate_identity = _stable(
                    "synthesis_candidate", subject_ref, attribute, value
                )
                existing = next(
                    (obj for obj in view.objects(type="profile_candidate")
                     if obj.data.get("candidate_identity") == candidate_identity),
                    None,
                )
                if existing is not None:
                    candidate_ref = existing.id
                else:
                    candidate = graph.add_object("profile_candidate", {
                        "candidate_identity": candidate_identity,
                        "text": str((row or {}).get("rationale") or f"{attribute}: {value}")[:500],
                        "confidence": 0.7,
                        "evidence_id": evidence.id,
                        "evidence_identity": str((evidence.data or {}).get("evidence_identity") or ""),
                        "revision_id": str((evidence.data or {}).get("revision_id") or ""),
                        "extraction_record_id": request_id,
                        "extractor_id": SYNTHESIS_PROJECTOR,
                        "extractor_version": SYNTHESIS_VERSION,
                        "extraction_config_id": f"draft@{DRAFT_VERSION}",
                        "status": "candidate",
                        "invalidation_reason": None,
                        "metadata": {"projector": DRAFT_COMPOSER, "synthesis_refs": refs},
                        "attribute": attribute,
                        "value": value,
                    })
                    candidate_ref = candidate.id
        if candidate_ref is None:
            # No owner-attested anchor: the item still renders for review but
            # routes through the owner-declaration path at submit.
            pass
        rationale, rationale_flags = _sanitize((row or {}).get("rationale") or "", 500)
        _mint_item(
            graph, view, draft=draft, section="identity",
            proposed={"attribute": attribute, "value": value},
            rationale=rationale, evidence_refs=refs,
            confidence=float((row or {}).get("confidence") or 0.7),
            uncertainty=str((row or {}).get("uncertainty") or "")[:300],
            candidate_ref=candidate_ref, flags=value_flags + rationale_flags,
        )
        counts["identity"] += 1

    for section, value_key in (
        ("narrative", "statement"), ("instructions", "instruction"),
    ):
        for row in (sections.get(section) or [])[:cap]:
            value, flags = _sanitize((row or {}).get(value_key) or "", 600)
            refs = _refs_of(row or {})
            if not value:
                continue
            if not refs:
                dropped_uncited += 1
                continue
            rationale, rationale_flags = _sanitize((row or {}).get("rationale") or "", 500)
            _mint_item(
                graph, view, draft=draft, section=section,
                proposed={"attribute": _SECTION_ATTRIBUTES[section], "value": value},
                rationale=rationale, evidence_refs=refs,
                confidence=float((row or {}).get("confidence") or 0.6),
                uncertainty=str((row or {}).get("uncertainty") or "")[:300],
                candidate_ref=None, flags=flags + rationale_flags,
            )
            counts[section] += 1

    for row in (sections.get("projects") or [])[:cap]:
        name = " ".join(str((row or {}).get("name") or "").split())
        refs = _refs_of(row or {})
        if not name or len(name) < 2:
            continue
        if not refs:
            dropped_uncited += 1
            continue
        description, desc_flags = _sanitize((row or {}).get("description") or "", 800)
        key = " ".join(name.lower().split())
        existing = next(
            (obj for obj in view.objects(type="project_candidate")
             if " ".join(str(obj.data.get("name") or "").lower().split()) == key
             and obj.data.get("status") == "proposed"),
            None,
        )
        if existing is not None:
            graph.patch_object(existing.id, {
                "kind": "synthesized",
                "description": description,
                "score_milli": max(800, int(existing.data.get("score_milli") or 0)),
                "sources": list(dict.fromkeys(
                    [*(existing.data.get("sources") or []), *refs]
                )),
            }, rationale="the setup draft refined this proposal")
            candidate_ref = existing.id
        else:
            candidate = graph.add_object("project_candidate", {
                "candidate_identity": _stable("draft_project", subject_ref, key),
                "name": name,
                "kind": "synthesized",
                "score_milli": 800,
                "sources": refs,
                "rationale": str((row or {}).get("rationale") or "proposed by the setup draft")[:500],
                "status": "proposed",
                "description": description,
                "project_id": None,
                "metadata": {"projector": DRAFT_COMPOSER},
            })
            candidate_ref = candidate.id
        rationale, rationale_flags = _sanitize((row or {}).get("rationale") or "", 500)
        _mint_item(
            graph, view, draft=draft, section="projects",
            proposed={
                "name": name, "description": description,
                "status_note": str((row or {}).get("status_note") or "")[:200],
                "people": [str(p)[:120] for p in ((row or {}).get("people") or [])[:8]],
            },
            rationale=rationale, evidence_refs=refs,
            confidence=float((row or {}).get("confidence") or 0.7),
            uncertainty=str((row or {}).get("uncertainty") or "")[:300],
            candidate_ref=candidate_ref, flags=desc_flags + rationale_flags,
        )
        counts["projects"] += 1

    for row in (sections.get("people") or [])[:cap]:
        name, name_flags = _sanitize((row or {}).get("name") or "", 120)
        relationship, rel_flags = _sanitize((row or {}).get("relationship") or "", 300)
        refs = _refs_of(row or {})
        if not name:
            continue
        if not refs:
            dropped_uncited += 1
            continue
        rationale, rationale_flags = _sanitize((row or {}).get("rationale") or "", 500)
        _mint_item(
            graph, view, draft=draft, section="people",
            proposed={"attribute": "person", "value": name, "relationship": relationship},
            rationale=rationale, evidence_refs=refs,
            confidence=float((row or {}).get("confidence") or 0.6),
            uncertainty=str((row or {}).get("uncertainty") or "")[:300],
            candidate_ref=None, flags=name_flags + rel_flags + rationale_flags,
        )
        counts["people"] += 1

    for row in (sections.get("access") or [])[:cap]:
        strategy, strategy_flags = _sanitize((row or {}).get("strategy") or "", 300)
        source, source_flags = _sanitize((row or {}).get("source") or "", 120)
        question_class, q_flags = _sanitize((row or {}).get("question_class") or "", 200)
        refs = _refs_of(row or {})
        if not strategy or not source:
            continue
        if not refs:
            dropped_uncited += 1
            continue
        rationale, rationale_flags = _sanitize((row or {}).get("rationale") or "", 500)
        _mint_item(
            graph, view, draft=draft, section="access",
            proposed={
                "question_class": question_class, "source": source,
                "strategy": strategy,
            },
            rationale=rationale, evidence_refs=refs,
            confidence=float((row or {}).get("confidence") or 0.6),
            uncertainty=str((row or {}).get("uncertainty") or "")[:300],
            candidate_ref=None,
            flags=strategy_flags + source_flags + q_flags + rationale_flags,
        )
        counts["access"] += 1

    total = sum(counts.values())
    run = graph.add_object("subject_synthesis_run", {
        "run_identity": _stable("draft_run", request_id, draft.id),
        "subject_ref": subject_ref,
        "status": "completed" if not error else ("completed" if total else "failed"),
        "model": outcome.get("model"),
        "inputs": {
            "facts": len(payload.get("facts") or []),
            "research_findings": len(payload.get("research") or []),
            "comprehension_rows": len(payload.get("comprehension") or []),
            "signal_maps": len(payload.get("signal_maps") or []),
            "entities": len(payload.get("entities") or []),
            "packing": payload.get("packing") or {},
        },
        "proposed": {**counts, "dropped_uncited": dropped_uncited},
        "noise": [],
        "error": str(error)[:300] if error else None,
        "metadata": {
            "kind": "setup_draft",
            "draft_id": draft.id,
            "response_sample": str(outcome.get("response_sample") or "")[:400],
        },
    })
    graph.patch_object(draft.id, {
        "run_id": run.id,
        "counts": {"items": total, **counts, "dropped_uncited": dropped_uncited},
    })
    graph.patch_object(request_id, {
        "status": "completed", "run_id": run.id,
        "metadata": {"kind": "setup_draft", "draft_id": draft.id},
    }, rationale="setup draft composed")
    return {
        "ok": True, "draft_id": draft.id, "run_id": run.id,
        "items": total, "dropped_uncited": dropped_uncited,
    }


# ---- the zero-key floor -----------------------------------------------------

def compose_deterministic_draft_fn(
    graph, *, subject_ref: str = "owner", reader=None
) -> dict[str, Any]:
    """The smaller deterministic draft (ADR 0046 §5): pending candidates and
    connected-source hints composed without a model, through the identical
    review path. Never a dead end — an empty draft is still resolvable."""
    from packs.subject_profile.projection import classify_subject_attribute

    view = reader or graph
    draft = _mint_draft(
        graph, subject_ref=subject_ref, source="deterministic", run_id=None,
        included_refs=[], coverage={"composer": "deterministic_floor"},
    )
    counts = {section: 0 for section in DRAFT_SECTIONS}

    class_to_section = {
        "identity": "identity", "narrative": "narrative",
        "instruction": "instructions",
    }
    for candidate in view.objects(type="profile_candidate"):
        data = candidate.data or {}
        if data.get("status") != "candidate":
            continue
        attribute = str(data.get("attribute") or "profile_statement")
        section = class_to_section[classify_subject_attribute(attribute)]
        value = str(data.get("value") or data.get("text") or "").strip()
        if not value:
            continue
        _mint_item(
            graph, view, draft=draft, section=section,
            proposed={"attribute": attribute, "value": value},
            rationale=str(data.get("text") or "pending from your review queue")[:500],
            evidence_refs=[ref for ref in [data.get("evidence_id")] if ref] or [candidate.id],
            confidence=float(data.get("confidence") or 0.6),
            uncertainty="",
            candidate_ref=candidate.id, flags=[],
        )
        counts[section] += 1

    for candidate in view.objects(type="project_candidate"):
        data = candidate.data or {}
        if data.get("status") != "proposed":
            continue
        _mint_item(
            graph, view, draft=draft, section="projects",
            proposed={
                "name": str(data.get("name") or ""),
                "description": str(data.get("description") or "")[:800],
                "status_note": "", "people": [],
            },
            rationale=str(data.get("rationale") or "derived from your confirmed material")[:500],
            evidence_refs=list(data.get("sources") or [])[:10] or [candidate.id],
            confidence=0.6, uncertainty="",
            candidate_ref=candidate.id, flags=[],
        )
        counts["projects"] += 1

    for profile in view.objects(type="integration_profile"):
        data = profile.data or {}
        if data.get("status") != "active":
            continue
        service = str(data.get("service") or "")
        surfaces = [
            str(row.get("surface") or "")
            for row in (data.get("signal_map") or [])
            if row.get("candidate_types")
        ][:2]
        if not surfaces:
            continue
        _mint_item(
            graph, view, draft=draft, section="access",
            proposed={
                "question_class": f"questions about your {service} activity",
                "source": service,
                "strategy": f"search {service} {', '.join(surfaces)}",
            },
            rationale=f"{service} is connected and its surfaces carry signal",
            evidence_refs=[profile.id],
            confidence=0.6, uncertainty="",
            candidate_ref=None, flags=[],
        )
        counts["access"] += 1

    total = sum(counts.values())
    graph.patch_object(draft.id, {"counts": {"items": total, **counts}})
    return {"ok": True, "draft_id": draft.id, "items": total, "source": "deterministic"}


# ---- review -----------------------------------------------------------------

def review_setup_item_fn(
    graph, item_ref: str, verdict: str, *,
    actor: str = "owner", edited_value: Optional[dict[str, Any]] = None,
    reader=None,
) -> dict[str, Any]:
    """Owner verdict on one item. Accept/reject settles the pre-recorded
    prediction; edit supersedes the proposal as an owner declaration and is
    scored as its own verdict class — never as a correct 'accept'."""
    view = reader or graph
    item = view.get_object(item_ref) if hasattr(view, "get_object") else None
    if item is None or getattr(item, "type", None) != "setup_draft_item":
        return {"ok": False, "reason": "item_not_found"}
    if verdict not in ("accept", "reject", "edit", "defer"):
        raise ValueError("verdict must be accept | reject | edit | defer")
    status = item.data.get("status")
    if status not in ("proposed", "accepted", "rejected", "edited", "deferred"):
        return {"ok": False, "reason": "item_already_committed", "status": status}
    patch: dict[str, Any] = {
        "verdict": verdict, "verdict_actor": actor,
        "status": {"accept": "accepted", "reject": "rejected",
                   "edit": "edited", "defer": "deferred"}[verdict],
    }
    if verdict == "edit":
        if not isinstance(edited_value, dict) or not edited_value:
            raise ValueError("edit requires edited_value")
        merged = {**dict(item.data.get("proposed") or {}), **edited_value}
        patch["edited_value"] = merged
    graph.patch_object(item.id, patch, rationale=f"setup item {verdict} by {actor}")
    return {"ok": True, "item_id": item.id, "status": patch["status"]}


def reclassify_setup_item_fn(
    graph, item_ref: str, section: str, *, actor: str = "owner", reader=None
) -> dict[str, Any]:
    """Owner reclassification: the item moves to another section/destination
    (e.g. a 'narrative' line that is really an instruction)."""
    view = reader or graph
    item = view.get_object(item_ref) if hasattr(view, "get_object") else None
    if item is None or getattr(item, "type", None) != "setup_draft_item":
        return {"ok": False, "reason": "item_not_found"}
    if section not in DRAFT_SECTIONS:
        raise ValueError(f"section must be one of {DRAFT_SECTIONS}")
    if item.data.get("status") not in ("proposed", "accepted", "edited", "deferred", "rejected"):
        return {"ok": False, "reason": "item_already_committed"}
    proposed = dict(item.data.get("proposed") or {})
    if section in _SECTION_ATTRIBUTES and "value" in proposed:
        proposed["attribute"] = _SECTION_ATTRIBUTES[section]
    graph.patch_object(item.id, {
        "section": section,
        "destination": SECTION_DESTINATIONS[section],
        "proposed": proposed,
        # A moved item's candidate belonged to the OLD destination; the new
        # destination promotes through its own path at submit.
        "candidate_ref": item.data.get("candidate_ref") if section in ("identity", "projects") else None,
    }, rationale=f"reclassified to {section} by {actor}")
    return {"ok": True, "item_id": item.id, "section": section}


def merge_setup_project_items_fn(
    graph, item_refs: list[str], name: str, *, actor: str = "owner", reader=None
) -> dict[str, Any]:
    """Merge N project items into one owner-edited item; the merged sources
    and evidence union, the originals become superseded."""
    view = reader or graph
    items = []
    for ref in item_refs:
        item = view.get_object(ref) if hasattr(view, "get_object") else None
        if item is None or getattr(item, "type", None) != "setup_draft_item":
            return {"ok": False, "reason": "item_not_found", "item_ref": ref}
        if item.data.get("section") != "projects":
            return {"ok": False, "reason": "not_a_project_item", "item_ref": ref}
        items.append(item)
    if len(items) < 2:
        raise ValueError("merging needs at least two project items")
    name = " ".join(str(name).split())
    if not name:
        raise ValueError("the merged project needs a name")
    primary = items[0]
    refs = list(dict.fromkeys(
        ref for item in items for ref in item.data.get("evidence_refs") or []
    ))
    descriptions = [
        str((item.data.get("proposed") or {}).get("description") or "")
        for item in items
    ]
    merged_value = {
        **dict(primary.data.get("proposed") or {}),
        "name": name,
        "description": " ".join(d for d in descriptions if d)[:800],
        "merged_from": [item.id for item in items],
    }
    graph.patch_object(primary.id, {
        "verdict": "edit", "verdict_actor": actor, "status": "edited",
        "edited_value": merged_value,
        "evidence_refs": refs[:10],
    }, rationale=f"merged {len(items)} project items by {actor}")
    for item in items[1:]:
        graph.patch_object(item.id, {
            "status": "superseded", "verdict": "edit", "verdict_actor": actor,
            "metadata": {**dict(item.data.get("metadata") or {}),
                         "merged_into": primary.id},
        }, rationale="merged into a sibling project item")
    return {"ok": True, "item_id": primary.id, "merged": len(items) - 1}


# ---- submission -------------------------------------------------------------

def defer_setup_draft_fn(
    graph, draft_ref: str, *, actor: str = "owner", reader=None
) -> dict[str, Any]:
    """Explicit deferral: a durable resolution (ADR 0046 §5). Unresolved
    items stay reviewable from the workspace; the ceremony may proceed."""
    view = reader or graph
    draft = _draft_by_ref(view, draft_ref)
    if draft is None:
        return {"ok": False, "reason": "draft_not_found"}
    if draft.data.get("status") in ("submitted", "superseded"):
        return {"ok": False, "reason": "already_resolved",
                "status": draft.data.get("status")}
    graph.patch_object(draft.id, {"status": "deferred"},
                       rationale=f"setup draft deferred by {actor}")
    return {"ok": True, "draft_id": draft.id, "status": "deferred",
            "resolution": "deferred"}


def begin_setup_draft_submission_fn(
    graph, draft_ref: str, *, actor: str = "owner", reader=None
) -> dict[str, Any]:
    """Submission stage 1: mark the draft submitting, defer unresolved items,
    reject the candidates behind rejected items, and stage owner-declaration
    evidence for accepted/edited declaration-path items. The host drains the
    runtime between stages (packs own contracts; hosts own sequencing)."""
    view = reader or graph
    draft = _draft_by_ref(view, draft_ref)
    if draft is None:
        return {"ok": False, "reason": "draft_not_found"}
    if draft.data.get("status") in ("submitted", "superseded"):
        return {"ok": False, "reason": "already_resolved",
                "status": draft.data.get("status")}
    graph.patch_object(draft.id, {"status": "submitting"},
                       rationale=f"setup draft submitted by {actor}")
    staged = 0
    for item in _items_for_draft(view, draft.id):
        data = item.data or {}
        status = data.get("status")
        if status == "proposed":
            graph.patch_object(item.id, {
                "status": "deferred", "verdict": "defer", "verdict_actor": actor,
            }, rationale="unresolved at submission; explicitly deferred")
            continue
        if status not in ("accepted", "edited"):
            continue
        section = data.get("section")
        needs_declaration = (
            section in _DECLARATION_SECTIONS
            or (section == "identity" and not data.get("candidate_ref"))
        )
        if not needs_declaration:
            continue
        proposed = dict(data.get("edited_value") or data.get("proposed") or {})
        value = str(proposed.get("value") or "").strip()
        if not value:
            continue
        text = value
        if proposed.get("relationship"):
            text = f"{value} — {proposed['relationship']}"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        dedup_key = _stable("draft_declaration", item.id)
        item_obj = graph.add_object("acquired_item", {
            "source_surface_id": "setup_draft",
            "provider_item_id": dedup_key,
            "dedup_key": dedup_key,
            "source_ref": f"setup_draft_item:{item.id}",
            "source_hash": digest,
            "provider_time": None,
            "replay_mode": "inline",
            "replay_payload_ref": text,
            "replay_payload_hash": digest,
            "media_type": "text/plain",
            "importer_id": "setup_draft_declaration",
            "importer_version": DRAFT_VERSION,
        })
        graph.add_object("acquired_content", {
            "acquired_item_id": item_obj.id,
            "normalized_content": text,
            # The owner's acceptance IS the owner act: the declaration is
            # owner-scoped by construction, and the connector/research refs
            # stay on the item as the audit trail.
            "normalized_metadata": {
                "subject_scope": "owner_profile",
                "declared_via": "setup_draft",
                "draft_item_id": item.id,
                "derived_from_refs": list(data.get("evidence_refs") or [])[:10],
            },
            "source_category": "local_knowledge",
            "connection_path": "manual",
            "is_fixture": False,
        })
        graph.patch_object(item.id, {
            "metadata": {**dict(data.get("metadata") or {}),
                         "declaration_dedup_key": dedup_key},
        })
        staged += 1
    return {"ok": True, "draft_id": draft.id, "staged_declarations": staged,
            "needs_drain": staged > 0}


def complete_setup_draft_submission_fn(
    graph, draft_ref: str, *, actor: str = "owner", reader=None
) -> dict[str, Any]:
    """Submission stage 2 (after the host drained the normalizer): promote
    every accepted/edited item through its destination's canonical path.
    Failures stay visible per item and the draft lands partial — resubmit
    retries exactly the failed items."""
    from packs.projects.tools import review_project_candidate_fn
    from packs.subject_profile.tools import review_subject_fact_fn

    view = reader or graph
    draft = _draft_by_ref(view, draft_ref)
    if draft is None:
        return {"ok": False, "reason": "draft_not_found"}
    committed = failed = 0
    for item in _items_for_draft(view, draft.id):
        data = item.data or {}
        status = data.get("status")
        if status == "rejected":
            candidate_ref = data.get("candidate_ref")
            if candidate_ref and not (data.get("metadata") or {}).get("candidate_rejected"):
                try:
                    candidate = graph.get_object(candidate_ref)
                    if candidate is not None and candidate.type == "profile_candidate":
                        review_subject_fact_fn(
                            graph, candidate_ref, "reject", decided_by=actor,
                            metadata={"setup_draft_item": item.id},
                        )
                    elif candidate is not None and candidate.type == "project_candidate":
                        review_project_candidate_fn(
                            graph, candidate_ref, "dismiss", actor=actor
                        )
                    graph.patch_object(item.id, {
                        "metadata": {**dict(data.get("metadata") or {}),
                                     "candidate_rejected": True},
                    })
                except Exception:
                    pass  # a rejection that cannot settle is not a commit failure
            continue
        if status not in ("accepted", "edited"):
            continue
        proposed = dict(data.get("edited_value") or data.get("proposed") or {})
        section = data.get("section")
        try:
            if section == "projects":
                candidate_ref = data.get("candidate_ref")
                if not candidate_ref:
                    raise ValueError("project item lost its candidate")
                candidate = graph.get_object(candidate_ref)
                if candidate is not None and candidate.data.get("status") == "confirmed":
                    result_ref = candidate.data.get("project_id")
                else:
                    result = review_project_candidate_fn(
                        graph, candidate_ref, "confirm", actor=actor,
                        name_override=str(proposed.get("name") or "") or None,
                        description=str(proposed.get("description") or ""),
                    )
                    result_ref = result.get("project_id")
            elif section == "access":
                hint = graph.add_object("information_access_hint", {
                    "hint_identity": _stable("access_hint", item.id),
                    "subject_ref": str(draft.data.get("subject_ref") or "owner"),
                    "question_class": str(proposed.get("question_class") or "")[:200],
                    "source": str(proposed.get("source") or "")[:120],
                    "strategy": str(proposed.get("strategy") or "")[:300],
                    "evidence_refs": list(data.get("evidence_refs") or [])[:10],
                    "accepted_by": actor,
                    "draft_item_id": item.id,
                    "status": "active",
                    "metadata": {"edited": status == "edited"},
                })
                result_ref = hint.id
            else:
                # subject_profile destinations: an existing owner-attested
                # candidate takes the direct verdict; declaration-path items
                # promote through the evidence staged at begin.
                candidate_ref = data.get("candidate_ref")
                corrected = None
                if status == "edited":
                    corrected = str(proposed.get("value") or "") or None
                if not candidate_ref:
                    dedup_key = (data.get("metadata") or {}).get("declaration_dedup_key")
                    if not dedup_key:
                        raise ValueError("declaration evidence was never staged")
                    evidence = next(
                        (obj for obj in graph.objects(type="activity_evidence")
                         if (obj.data.get("normalized_metadata") or {}).get("draft_item_id") == item.id),
                        None,
                    )
                    if evidence is None:
                        raise ValueError(
                            "declaration evidence not yet normalized; drain and resubmit"
                        )
                    candidate = graph.add_object("profile_candidate", {
                        "candidate_identity": _stable("draft_declaration_candidate", item.id),
                        "text": str(proposed.get("value") or "")[:500],
                        "confidence": 0.9,
                        "evidence_id": evidence.id,
                        "evidence_identity": str((evidence.data or {}).get("evidence_identity") or ""),
                        "revision_id": str((evidence.data or {}).get("revision_id") or ""),
                        "extraction_record_id": draft.id,
                        "extractor_id": DRAFT_COMPOSER,
                        "extractor_version": DRAFT_VERSION,
                        "extraction_config_id": f"draft@{DRAFT_VERSION}",
                        "status": "candidate",
                        "invalidation_reason": None,
                        "metadata": {
                            "declaration": True,
                            "derived_from_refs": list(data.get("evidence_refs") or [])[:10],
                        },
                        "attribute": str(proposed.get("attribute") or "profile_statement"),
                        "value": str(proposed.get("value") or ""),
                    })
                    candidate_ref = candidate.id
                    graph.patch_object(item.id, {"candidate_ref": candidate_ref})
                verdict_metadata: dict[str, Any] = {"setup_draft_item": item.id}
                if status == "edited" or section in _DECLARATION_SECTIONS:
                    # Owner declarations/edits carry provenance but mint no
                    # synthetic prediction wins (ADR 0046 §3).
                    verdict_metadata["owner_declaration"] = True
                result = review_subject_fact_fn(
                    graph, candidate_ref, "confirm",
                    corrected_value=corrected, decided_by=actor,
                    metadata=verdict_metadata,
                )
                result_ref = result.get("verdict_id")
            graph.patch_object(item.id, {
                "status": "committed",
                "metadata": {**dict(item.data.get("metadata") or {}),
                             "result_ref": result_ref},
                "commit_error": None,
            }, rationale="promoted through its canonical pipeline")
            committed += 1
        except Exception as exc:
            graph.patch_object(item.id, {
                "status": "commit_failed",
                "commit_error": f"{type(exc).__name__}: {exc}"[:300],
            }, rationale="promotion failed; restartable")
            failed += 1
    draft_status = "partial" if failed else "submitted"
    graph.patch_object(draft.id, {"status": draft_status},
                       rationale="setup draft submission settled")
    return {
        "ok": failed == 0, "draft_id": draft.id, "status": draft_status,
        "committed": committed, "failed": failed,
        "resolution": "submitted" if failed == 0 else "partial",
    }


def resubmit_setup_draft_fn(
    graph, draft_ref: str, *, actor: str = "owner", reader=None
) -> dict[str, Any]:
    """Retry exactly the failed items of a partial submission."""
    view = reader or graph
    draft = _draft_by_ref(view, draft_ref)
    if draft is None:
        return {"ok": False, "reason": "draft_not_found"}
    if draft.data.get("status") != "partial":
        return {"ok": False, "reason": "not_partial", "status": draft.data.get("status")}
    for item in _items_for_draft(view, draft.id):
        if item.data.get("status") == "commit_failed":
            restored = "edited" if item.data.get("edited_value") else "accepted"
            graph.patch_object(item.id, {"status": restored, "commit_error": None})
    graph.patch_object(draft.id, {"status": "submitting"})
    return complete_setup_draft_submission_fn(
        graph, draft_ref, actor=actor, reader=reader
    )


def project_setup_draft_fn(reader, *, subject_ref: str = "owner") -> dict[str, Any]:
    """The neutral read shape clients render: the head draft, its items by
    section, and the resolution state the ceremony gates on."""
    draft = current_setup_draft_fn(reader, subject_ref=subject_ref)
    if draft is None:
        return {"draft": None, "items": [], "resolved": False}
    items = [
        {
            "id": item.id,
            "section": item.data.get("section"),
            "destination": item.data.get("destination"),
            "proposed": item.data.get("proposed"),
            "edited_value": item.data.get("edited_value"),
            "rationale": item.data.get("rationale"),
            "evidence_refs": item.data.get("evidence_refs"),
            "confidence": item.data.get("confidence"),
            "uncertainty": item.data.get("uncertainty"),
            "status": item.data.get("status"),
            "verdict": item.data.get("verdict"),
            "predicted_verdict": item.data.get("predicted_verdict"),
            "predicted_confidence_percent": item.data.get("predicted_confidence_percent"),
            "injection_flags": item.data.get("injection_flags") or [],
            "commit_error": item.data.get("commit_error"),
        }
        for item in _items_for_draft(reader, draft.id)
    ]
    status = str(draft.data.get("status") or "proposed")
    return {
        "draft": {
            "id": draft.id,
            "draft_identity": draft.data.get("draft_identity"),
            "version": draft.data.get("version"),
            "status": status,
            "source": draft.data.get("source"),
            "counts": draft.data.get("counts"),
            "coverage": draft.data.get("coverage"),
        },
        "items": items,
        "resolved": status in ("submitted", "deferred", "partial"),
    }


__all__ = [
    "DRAFT_SECTIONS",
    "SECTION_DESTINATIONS",
    "begin_setup_draft_submission_fn",
    "commit_setup_draft_fn",
    "compose_deterministic_draft_fn",
    "current_setup_draft_fn",
    "defer_setup_draft_fn",
    "merge_setup_project_items_fn",
    "perform_setup_draft",
    "prepare_setup_draft_fn",
    "project_setup_draft_fn",
    "reclassify_setup_item_fn",
    "request_setup_draft_fn",
    "resubmit_setup_draft_fn",
    "review_setup_item_fn",
]
