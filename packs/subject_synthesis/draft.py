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

#: The typed correction reasons a rejection may carry (ADR 0048 §4). Each is
#: a distinct teaching signal for the prediction/routing loop — a binary
#: "no" throws away exactly the information the system needs.
CORRECTION_REASONS = (
    "not_me", "duplicate", "incorrect", "not_useful",
    "wrong_type", "wrong_grouping",
)

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


# ---- stable semantic identity (ADR 0048 §3) -----------------------------------

def semantic_item_key(section: str, proposed: dict[str, Any]) -> str:
    """The content-derived identity a verdict may follow across versions.

    Deliberately narrow: an edited identity value, a renamed project, or a
    rewritten statement is a NEW semantic item — a verdict never carries
    onto materially changed content merely because an index or generated id
    matched. Only presentation-level fields (a project's description, an
    access hint's question class) may change under a stable key.
    """
    proposed = proposed or {}

    def _norm(value: Any) -> str:
        return " ".join(str(value or "").split()).casefold()

    if section == "projects":
        material = ("name", _norm(proposed.get("name")))
    elif section == "access":
        material = (
            "access", _norm(proposed.get("source")), _norm(proposed.get("strategy"))
        )
    elif section == "people":
        material = ("person", _norm(proposed.get("value") or proposed.get("name")))
    else:  # identity / narrative / instructions: the value IS the identity
        material = (
            _norm(proposed.get("attribute")), _norm(proposed.get("value")),
        )
    digest = hashlib.sha256(
        "\x1f".join((section, *material)).encode("utf-8")
    ).hexdigest()
    return f"sem_{digest[:32]}"


def _semantic_presentation(section: str, proposed: dict[str, Any]) -> str:
    """The comparable presentation content under a stable key — what makes a
    same-key item 'changed'."""
    proposed = proposed or {}
    if section == "projects":
        fields = {k: proposed.get(k) for k in ("description", "status_note", "people")}
    elif section == "access":
        fields = {"question_class": proposed.get("question_class")}
    elif section == "people":
        fields = {"relationship": proposed.get("relationship")}
    else:
        return ""  # key == content: same key means unchanged
    return json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str)


def draft_review_started_fn(reader, draft) -> bool:
    """Whether review began on this draft: an explicit begin receipt, any
    verdict, or any owner comment. From that moment the snapshot is frozen
    (ADR 0048 §3)."""
    if (draft.data.get("metadata") or {}).get("review_started"):
        return True
    for item in _items_for_draft(reader, draft.id):
        data = item.data or {}
        if data.get("verdict") is not None:
            return True
        if data.get("comments"):
            return True
        if data.get("status") not in ("proposed",):
            return True
    return False


def begin_setup_review_fn(
    graph, draft_ref: str, *, actor: str = "owner", reader=None,
) -> dict[str, Any]:
    """Explicitly start review: pins the snapshot before any verdict lands
    (the client calls this when the review surface opens). Idempotent."""
    view = reader or graph
    draft = _draft_by_ref(view, draft_ref)
    if draft is None:
        return {"ok": False, "reason": "draft_not_found"}
    if draft.data.get("status") != "proposed":
        return {"ok": True, "frozen": True,
                "status": draft.data.get("status"), "already_resolved": True}
    metadata = dict(draft.data.get("metadata") or {})
    if metadata.get("review_started"):
        return {"ok": True, "draft_id": draft.id, "frozen": True}
    metadata["review_started"] = {"by": actor}
    graph.patch_object(draft.id, {"metadata": metadata},
                       rationale=f"review begun by {actor}; snapshot frozen")
    return {"ok": True, "draft_id": draft.id, "frozen": True}


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

def synthesis_input_fingerprint_fn(reader, *, subject_ref: str = "owner") -> str:
    """A stable digest of every EXTERNAL input a draft synthesis may consume:
    source-lens contributions, promoted owner facts, answered owner
    questions, and source-derived (never synthesis-authored) candidates.

    This is the convergence rule's anchor (the 2026-07-14 live run ran 28
    recompositions of one draft): a synthesis records the fingerprint it
    consumed, and no new synthesis is owed until the fingerprint moves —
    which only genuinely new source/owner material can do. Synthesis outputs
    (synthesized candidates, drafts, deltas, working versions) are
    deliberately absent from this digest."""
    parts: list[str] = []
    for lens in reader.objects(type="source_lens"):
        data = lens.data or {}
        keys = sorted(
            str(row.get("entry_key") or row.get("statement") or "")
            for row in data.get("contributions") or []
        )
        parts.append(f"lens:{data.get('affordance_id')}:{data.get('status')}:"
                     + "|".join(keys))
    for fact in reader.objects(type="subject_fact"):
        data = fact.data or {}
        if data.get("subject_ref") == subject_ref and data.get("status") == "promoted":
            parts.append(f"fact:{data.get('attribute')}:{data.get('value')}")
    for question in reader.objects(type="owner_question"):
        data = question.data or {}
        if data.get("status") == "answered":
            answer = dict(data.get("answer") or {})
            parts.append(f"answer:{question.id}:{answer.get('option_id')}:"
                         f"{answer.get('text')}")
    for candidate in reader.objects(type="project_candidate"):
        data = candidate.data or {}
        if data.get("kind") != "synthesized":
            parts.append(f"pc:{candidate.id}:{data.get('status')}")
    for candidate in reader.objects(type="profile_candidate"):
        data = candidate.data or {}
        authored_by_synthesis = (
            str((data.get("metadata") or {}).get("projector") or "")
            .startswith("subject_synthesis")
            or str(data.get("extractor_id") or "").startswith("subject_synthesis")
        )
        if not authored_by_synthesis:
            parts.append(f"fc:{candidate.id}:{data.get('status')}")
    digest = hashlib.sha256(
        "\n".join(sorted(parts)).encode("utf-8")
    ).hexdigest()
    return digest


def consumed_input_fingerprint_fn(reader, *, subject_ref: str = "owner") -> str:
    """The fingerprint the current head draft last consumed (stamped at
    compose/delta time). Empty for pre-fingerprint stores."""
    head = current_setup_draft_fn(reader, subject_ref=subject_ref)
    if head is None:
        return ""
    return str((head.data.get("coverage") or {}).get("input_fingerprint") or "")


def request_setup_draft_fn(
    graph, *, subject_ref: str = "owner", reason: str = "", reader=None
) -> dict[str, Any]:
    """Request the cross-source draft pass; hosts settle it on their pump.
    Idempotent while one draft request is open for the subject, and REFUSED
    when nothing external changed since the head draft consumed its inputs —
    one pending synthesis per input horizon, never a self-feeding chain."""
    view = reader or graph
    for obj in view.objects(type="subject_synthesis_request"):
        data = obj.data or {}
        if (
            data.get("subject_ref") == subject_ref
            and data.get("status") == "proposed"
            and (data.get("metadata") or {}).get("kind") == "setup_draft"
        ):
            return {"ok": True, "request_id": obj.id, "already_open": True}
    fingerprint = synthesis_input_fingerprint_fn(view, subject_ref=subject_ref)
    consumed = consumed_input_fingerprint_fn(view, subject_ref=subject_ref)
    if consumed and consumed == fingerprint:
        return {"ok": True, "request_id": None,
                "skipped": "no_new_source_material",
                "input_fingerprint": fingerprint}
    request = graph.add_object("subject_synthesis_request", {
        "request_identity": _stable("setup_draft_request", subject_ref, reason),
        "subject_ref": subject_ref,
        "reason": reason or "compose the setup draft",
        "status": "proposed",
        "metadata": {"kind": "setup_draft", "input_fingerprint": fingerprint},
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
    # The fingerprint of what this pass actually consumes, stamped onto the
    # draft at commit — the convergence rule's receipt.
    base["input_fingerprint"] = synthesis_input_fingerprint_fn(
        view, subject_ref=str(base.get("subject_ref") or "owner"),
    )

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
        "access": [{"question_class": "the information class this serves, e.g. 'current LP conversations'",
                    "source": "which connected source/surface",
                    "strategy": "one bounded retrieval action (a search/label/thread strategy), NEVER a bare label inventory",
                    "refs": ["…"], "rationale": "why this strategy is useful (required)"}],
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
        resolve_model_for_role, response_finish_reason, response_text,
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
    text = response_text(response)
    parsed = parse_json_payload(text) or {}
    return {
        "ok": True,
        "model": model,
        "sections": {key: list(parsed.get(key) or []) for key in DRAFT_SECTIONS},
        "response_sample": text[:400],
        "response_length": len(text),
        "finish_reason": response_finish_reason(response),
        # Host-measured spend: budgets charge from THIS, never from a
        # model-proposed cost field.
        "usage": {
            "tokens": int(getattr(response, "input_tokens", 0) or 0)
            + int(getattr(response, "output_tokens", 0) or 0),
            "cost_milli": float(getattr(response, "cost_usd", 0) or 0) * 1000.0,
            "seconds": float(getattr(response, "latency_seconds", 0) or 0),
        },
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

    def _fail(reason: str) -> dict[str, Any]:
        graph.patch_object(request_id, {
            "status": "failed", "error": str(reason)[:300],
        }, rationale="setup draft pass failed")
        return {"ok": False, "reason": "perform_failed", "error": reason}

    if error and not any(sections.values()):
        return _fail(str(error))
    # An empty strong pass is a FAILURE, not an empty draft: the live keyed
    # run committed "draft v1 · 0 proposals" and the owner had nothing to
    # review while the deterministic floor sat unused. Zero proposals — or
    # proposals citing nothing we packed — fail the request so the
    # deterministic composer resolves the gate instead (ADR 0046 §5).
    if not any(sections.values()):
        finish = str(outcome.get("finish_reason") or "")
        detail = f" finish_reason={finish}" if finish else ""
        length = int(outcome.get("response_length") or 0)
        return _fail(f"empty_synthesis_response length={length}{detail}")
    if not any(
        str(ref) in valid_refs
        for rows in sections.values()
        for row in rows or []
        for ref in ((row or {}).get("refs") or [])
    ):
        return _fail("synthesis_cited_nothing_packed")

    # A frozen review snapshot never moves (ADR 0048 §3): once the owner
    # started reviewing the head, later synthesis lands as an additive
    # understanding delta — never a superseding version.
    head = current_setup_draft_fn(view, subject_ref=subject_ref)
    if (
        head is not None
        and head.data.get("status") == "proposed"
        and draft_review_started_fn(view, head)
    ):
        delta_rows = _sanitized_rows_from_sections(sections, valid_refs, cap)
        delta = _mint_understanding_delta(
            graph, view, head=head, rows=delta_rows, source="synthesis",
            run_id=None, coverage={
                "packing": payload.get("packing") or {},
                "provenance": "model_assisted",
                "sources": draft_source_coverage_fn(view, subject_ref=subject_ref),
            },
        )
        # The frozen head has now CONSUMED this input horizon: without the
        # stamp, every progression tick re-runs the same synthesis into an
        # endless delta chain (the live-run loop).
        graph.patch_object(head.id, {
            "coverage": {
                **dict(head.data.get("coverage") or {}),
                "input_fingerprint": str(payload.get("input_fingerprint") or ""),
            },
        }, rationale="frozen head consumed this synthesis input horizon")
        graph.patch_object(request_id, {
            "status": "completed",
            "metadata": {"kind": "setup_draft", "delta_id": delta.get("delta_id")},
        }, rationale="late synthesis landed as an understanding delta")
        _charge_campaign_for_synthesis(graph, view, outcome)
        return {**delta, "draft_id": None, "frozen_head": head.id}

    draft = _mint_draft(
        graph, subject_ref=subject_ref, source="synthesis", run_id=None,
        included_refs=list(payload.get("included_refs") or []),
        coverage={
            "packing": payload.get("packing") or {},
            "research": payload.get("research_coverage") or [],
            "comprehension": payload.get("comprehension_coverage") or [],
            "provenance": "model_assisted",
            "sources": draft_source_coverage_fn(view, subject_ref=subject_ref),
            "input_fingerprint": str(payload.get("input_fingerprint") or ""),
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
            merged_sources = list(dict.fromkeys(
                [*(existing.data.get("sources") or []), *refs]
            ))
            # Same convergence rule as the recognition pass: patch only when
            # the draft genuinely adds evidence or a first description —
            # unconditional refreshes fed the recompose loop.
            if (
                merged_sources != list(existing.data.get("sources") or [])
                or (description and not existing.data.get("description"))
            ):
                graph.patch_object(existing.id, {
                    "kind": "synthesized",
                    "description": description or existing.data.get("description"),
                    "score_milli": max(800, int(existing.data.get("score_milli") or 0)),
                    "sources": merged_sources,
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

    dropped_low_quality = 0
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
        if not _access_row_is_actionable(question_class, strategy, rationale):
            # Raw source topology (a label inventory) is evidence, never a
            # proposed owner preference — the live run's owner rightly
            # rejected every one of these.
            dropped_low_quality += 1
            continue
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
        "proposed": {**counts, "dropped_uncited": dropped_uncited,
                     "dropped_low_quality": dropped_low_quality},
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
        "counts": {"items": total, **counts, "dropped_uncited": dropped_uncited,
                   "dropped_low_quality": dropped_low_quality},
    })
    graph.patch_object(request_id, {
        "status": "completed", "run_id": run.id,
        "metadata": {"kind": "setup_draft", "draft_id": draft.id},
    }, rationale="setup draft composed")
    _charge_campaign_for_synthesis(graph, view, outcome)
    return {
        "ok": True, "draft_id": draft.id, "run_id": run.id,
        "items": total, "dropped_uncited": dropped_uncited,
        "dropped_low_quality": dropped_low_quality,
    }


def _access_row_is_actionable(
    question_class: str, strategy: str, rationale: str,
) -> bool:
    """A 'where to look' proposal must be an actionable retrieval strategy:
    a question/information class it serves, a bounded strategy, and a reason.
    A bare label/folder inventory is source topology — evidence, not a
    user-facing preference."""
    import re

    if not question_class.strip() or not rationale.strip():
        return False
    if re.match(r"^\s*labels?\s*[:—-]", strategy, re.IGNORECASE):
        return False
    return True


def _charge_campaign_for_synthesis(graph, view, outcome: dict[str, Any]) -> None:
    """Charge the host-measured usage of one synthesis pass to the current
    campaign's spend ledger — model-proposed cost fields are never trusted."""
    usage = dict(outcome.get("usage") or {})
    if not usage:
        return
    try:
        from .coordinator import charge_campaign_usage_fn, current_campaign_fn

        campaign = current_campaign_fn(view)
        if campaign is None:
            return
        charge_campaign_usage_fn(
            graph, campaign.id,
            tokens=float(usage.get("tokens") or 0),
            cost_milli=float(usage.get("cost_milli") or 0),
            seconds=float(usage.get("seconds") or 0),
        )
    except Exception:
        pass  # budget accounting must never fail a committed synthesis


# ---- understanding deltas (ADR 0048 §3) ----------------------------------------

def _open_head_items_by_key(reader, draft_id: str) -> dict[str, Any]:
    index: dict[str, Any] = {}
    for item in _items_for_draft(reader, draft_id):
        if item.data.get("status") == "superseded":
            continue
        key = semantic_item_key(
            str(item.data.get("section") or ""),
            dict(item.data.get("edited_value") or item.data.get("proposed") or {}),
        )
        index.setdefault(key, item)
    return index


def _sanitized_rows_from_sections(
    sections: dict[str, Any], valid_refs: set[str], cap: int,
) -> list[dict[str, Any]]:
    """Normalize model sections into delta rows under the same sanitation
    and cite-or-drop rules the main commit path applies."""
    rows: list[dict[str, Any]] = []

    def _refs_of(row: dict[str, Any]) -> list[str]:
        return [str(ref) for ref in (row.get("refs") or []) if str(ref) in valid_refs]

    for row in (sections.get("identity") or [])[:cap]:
        attribute = str((row or {}).get("attribute") or "").strip().lower()
        value, _ = _sanitize((row or {}).get("value") or "", 300)
        refs = _refs_of(row or {})
        if not attribute or not value or not refs:
            continue
        rows.append({
            "section": "identity",
            "proposed": {"attribute": attribute, "value": value},
            "rationale": str((row or {}).get("rationale") or ""),
            "evidence_refs": refs,
            "confidence": float((row or {}).get("confidence") or 0.7),
            "uncertainty": str((row or {}).get("uncertainty") or ""),
        })
    for section, value_key in (
        ("narrative", "statement"), ("instructions", "instruction"),
    ):
        for row in (sections.get(section) or [])[:cap]:
            value, _ = _sanitize((row or {}).get(value_key) or "", 600)
            refs = _refs_of(row or {})
            if not value or not refs:
                continue
            rows.append({
                "section": section,
                "proposed": {"attribute": _SECTION_ATTRIBUTES[section], "value": value},
                "rationale": str((row or {}).get("rationale") or ""),
                "evidence_refs": refs,
                "confidence": float((row or {}).get("confidence") or 0.6),
                "uncertainty": str((row or {}).get("uncertainty") or ""),
            })
    for row in (sections.get("projects") or [])[:cap]:
        name = " ".join(str((row or {}).get("name") or "").split())
        refs = _refs_of(row or {})
        if not name or len(name) < 2 or not refs:
            continue
        description, _ = _sanitize((row or {}).get("description") or "", 800)
        rows.append({
            "section": "projects",
            "proposed": {
                "name": name, "description": description,
                "status_note": str((row or {}).get("status_note") or "")[:200],
                "people": [str(p)[:120] for p in ((row or {}).get("people") or [])[:8]],
            },
            "rationale": str((row or {}).get("rationale") or ""),
            "evidence_refs": refs,
            "confidence": float((row or {}).get("confidence") or 0.7),
            "uncertainty": str((row or {}).get("uncertainty") or ""),
        })
    for row in (sections.get("people") or [])[:cap]:
        name, _ = _sanitize((row or {}).get("name") or "", 120)
        relationship, _ = _sanitize((row or {}).get("relationship") or "", 300)
        refs = _refs_of(row or {})
        if not name or not refs:
            continue
        rows.append({
            "section": "people",
            "proposed": {"attribute": "person", "value": name,
                         "relationship": relationship},
            "rationale": str((row or {}).get("rationale") or ""),
            "evidence_refs": refs,
            "confidence": float((row or {}).get("confidence") or 0.6),
            "uncertainty": str((row or {}).get("uncertainty") or ""),
        })
    for row in (sections.get("access") or [])[:cap]:
        strategy, _ = _sanitize((row or {}).get("strategy") or "", 300)
        source_name, _ = _sanitize((row or {}).get("source") or "", 120)
        question_class, _ = _sanitize((row or {}).get("question_class") or "", 200)
        refs = _refs_of(row or {})
        if not strategy or not source_name or not refs:
            continue
        rationale = str((row or {}).get("rationale") or "")
        if not _access_row_is_actionable(question_class, strategy, rationale):
            continue  # same quality gate as the main commit path
        rows.append({
            "section": "access",
            "proposed": {"question_class": question_class,
                         "source": source_name, "strategy": strategy},
            "rationale": rationale,
            "evidence_refs": refs,
            "confidence": float((row or {}).get("confidence") or 0.6),
            "uncertainty": str((row or {}).get("uncertainty") or ""),
        })
    return rows


def _mint_understanding_delta(
    graph, view, *, head, rows: list[dict[str, Any]], source: str,
    run_id: Optional[str], coverage: dict[str, Any],
) -> dict[str, Any]:
    """Diff freshly-synthesized rows against the frozen head by semantic
    key: new keys arrive as `new`, same-key presentation changes as
    `changed` (or `conflicting` when the owner already accepted the head
    item), and unchanged items are silently NOT in the delta — their
    verdicts simply stand."""
    head_index = _open_head_items_by_key(view, head.id)
    delta_items: list[dict[str, Any]] = []
    for row in rows:
        section = str(row.get("section") or "")
        proposed = dict(row.get("proposed") or {})
        key = semantic_item_key(section, proposed)
        predecessor = head_index.get(key)
        if predecessor is None:
            change = "new"
            predecessor_id = None
        else:
            head_presentation = _semantic_presentation(
                section,
                dict(predecessor.data.get("edited_value")
                     or predecessor.data.get("proposed") or {}),
            )
            if _semantic_presentation(section, proposed) == head_presentation:
                continue  # unchanged: the standing verdict is the answer
            predecessor_id = predecessor.id
            change = (
                "conflicting"
                if predecessor.data.get("status") in ("accepted", "edited")
                else "changed"
            )
        delta_items.append({
            "change": change,
            "semantic_key": key,
            "section": section,
            "proposed": proposed,
            "rationale": str(row.get("rationale") or "")[:500],
            "evidence_refs": list(row.get("evidence_refs") or [])[:10],
            "confidence": min(1.0, max(0.0, float(row.get("confidence") or 0.6))),
            "uncertainty": str(row.get("uncertainty") or "")[:300],
            "predecessor_item_id": predecessor_id,
        })
    if not delta_items:
        return {"ok": True, "delta_id": None, "items": 0,
                "note": "nothing new or changed against the snapshot"}
    # ONE consolidated update per frozen snapshot: the cumulative diff
    # supersedes every unresolved predecessor delta instead of stacking a
    # banner per synthesis version (the live run stacked 23). A predecessor
    # the owner deferred stays deferred UNLESS the new delta carries keys
    # the owner has not yet seen — genuinely new information earns exactly
    # one fresh notification.
    all_deltas = [
        obj for obj in view.objects(type="understanding_delta")
        if obj.data.get("draft_id") == head.id
    ]
    unresolved = [
        obj for obj in all_deltas
        if obj.data.get("status") in ("open", "deferred")
    ]
    seen_keys = {
        str(item.get("semantic_key") or "")
        for obj in unresolved
        for item in obj.data.get("items") or []
    }
    new_keys = {str(row.get("semantic_key") or "") for row in delta_items}
    all_deferred = bool(unresolved) and all(
        obj.data.get("status") == "deferred" for obj in unresolved
    )
    status = "deferred" if (all_deferred and new_keys <= seen_keys) else "open"
    delta = graph.add_object("understanding_delta", {
        "delta_identity": _stable("understanding_delta", head.id, len(all_deltas) + 1),
        "subject_ref": str(head.data.get("subject_ref") or "owner"),
        "draft_id": head.id,
        "version": len(all_deltas) + 1,
        "status": status,
        "source": source,
        "run_id": run_id,
        "items": delta_items[:60],
        "coverage": coverage,
        "resolved_by": "",
        "metadata": {"cumulative": True,
                     "supersedes": [obj.id for obj in unresolved]},
    })
    for obj in unresolved:
        graph.patch_object(obj.id, {
            "status": "superseded", "resolved_by": "system:cumulative",
            "metadata": {**dict(obj.data.get("metadata") or {}),
                         "superseded_by": delta.id},
        }, rationale="a newer cumulative update subsumes this one")
    return {"ok": True, "delta_id": delta.id, "items": len(delta_items),
            "superseded": len(unresolved), "status": status}


def _delta_by_ref(reader, delta_ref: str):
    getter = getattr(reader, "get_object", None)
    if callable(getter):
        try:
            obj = getter(delta_ref)
        except Exception:
            obj = None
        if obj is not None and getattr(obj, "type", None) == "understanding_delta":
            return obj
    return next(
        (obj for obj in reader.objects(type="understanding_delta")
         if obj.data.get("delta_identity") == delta_ref),
        None,
    )


def _ensure_project_candidate(
    graph, view, *, subject_ref: str, name: str, description: str,
    refs: list[str], rationale: str,
) -> str:
    """Find-or-mint the project candidate behind a draft item so submission
    can promote it through the canonical projects pipeline."""
    key = " ".join(name.lower().split())
    existing = next(
        (obj for obj in view.objects(type="project_candidate")
         if " ".join(str(obj.data.get("name") or "").lower().split()) == key
         and obj.data.get("status") == "proposed"),
        None,
    )
    if existing is not None:
        return existing.id
    candidate = graph.add_object("project_candidate", {
        "candidate_identity": _stable("draft_project", subject_ref, key),
        "name": name,
        "kind": "synthesized",
        "score_milli": 800,
        "sources": refs[:10] or ["setup_draft"],
        "rationale": rationale[:500] or "proposed during setup review",
        "status": "proposed",
        "description": description[:800],
        "project_id": None,
        "metadata": {"projector": DRAFT_COMPOSER},
    })
    return candidate.id


def apply_understanding_delta_fn(
    graph, delta_ref: str, *, actor: str = "owner", reader=None,
) -> dict[str, Any]:
    """Apply an open/deferred delta: its rows join the frozen draft as
    FRESH proposed items (never pre-decided), each carrying its delta
    provenance. Project rows mint/reuse their candidate so submission can
    promote them."""
    view = reader or graph
    delta = _delta_by_ref(view, delta_ref)
    if delta is None:
        return {"ok": False, "reason": "delta_not_found"}
    if delta.data.get("status") in ("applied", "dismissed", "superseded"):
        return {"ok": True, "already_resolved": True,
                "status": delta.data.get("status")}
    head = _draft_by_ref(view, str(delta.data.get("draft_id") or ""))
    if head is None:
        return {"ok": False, "reason": "draft_not_found"}
    if head.data.get("status") in ("submitted", "superseded"):
        return {"ok": False, "reason": "draft_already_resolved",
                "status": head.data.get("status")}
    subject_ref = str(head.data.get("subject_ref") or "owner")
    head_index = _open_head_items_by_key(view, head.id)
    minted = 0
    superseded = 0
    skipped_unchanged = 0
    skipped_owner_edited = 0
    applied_keys: set[str] = set()
    for row in delta.data.get("items") or []:
        section = str(row.get("section") or "")
        if section not in DRAFT_SECTIONS:
            continue
        proposed = dict(row.get("proposed") or {})
        key = semantic_item_key(section, proposed)
        if key in applied_keys:
            continue  # one active item per semantic key, even within one apply
        applied_keys.add(key)
        predecessor = head_index.get(key)
        pred_data = dict(predecessor.data or {}) if predecessor is not None else {}
        if predecessor is not None:
            head_presentation = _semantic_presentation(
                section,
                dict(pred_data.get("edited_value") or pred_data.get("proposed") or {}),
            )
            if _semantic_presentation(section, proposed) == head_presentation:
                skipped_unchanged += 1
                continue  # the standing verdict is the answer
            if pred_data.get("status") == "edited":
                # The owner rewrote this item; model content never overrides
                # an owner declaration.
                skipped_owner_edited += 1
                continue
        candidate_ref = None
        if section == "projects":
            candidate_ref = str(pred_data.get("candidate_ref") or "") or None
            if candidate_ref is None:
                candidate_ref = _ensure_project_candidate(
                    graph, view, subject_ref=subject_ref,
                    name=str(proposed.get("name") or ""),
                    description=str(proposed.get("description") or ""),
                    refs=list(row.get("evidence_refs") or []),
                    rationale=str(row.get("rationale") or ""),
                )
        item = _mint_item(
            graph, view, draft=head, section=section,
            proposed=proposed,
            rationale=str(row.get("rationale") or ""),
            evidence_refs=list(row.get("evidence_refs") or []) or [delta.id],
            confidence=float(row.get("confidence") or 0.6),
            uncertainty=str(row.get("uncertainty") or ""),
            candidate_ref=candidate_ref, flags=[],
        )
        patch: dict[str, Any] = {
            "metadata": {
                **dict(item.data.get("metadata") or {}),
                "delta_ref": delta.id,
                "delta_change": str(row.get("change") or "new"),
                **({"predecessor_item_id": predecessor.id}
                   if predecessor is not None else {}),
            },
        }
        if predecessor is not None:
            # Changed content supersedes its predecessor and CARRIES the
            # attached decision state — applying the update was the owner's
            # explicit act, so an accepted/rejected verdict travels onto the
            # refreshed content instead of duplicating the item.
            if pred_data.get("status") in ("accepted", "rejected", "deferred"):
                patch["status"] = pred_data.get("status")
                patch["verdict"] = pred_data.get("verdict")
                patch["verdict_actor"] = pred_data.get("verdict_actor")
                if pred_data.get("correction"):
                    patch["correction"] = pred_data.get("correction")
            if pred_data.get("comments"):
                patch["comments"] = list(pred_data.get("comments") or [])[-12:]
            graph.patch_object(predecessor.id, {
                "status": "superseded",
                "metadata": {**dict(pred_data.get("metadata") or {}),
                             "superseded_by": item.id},
            }, rationale="an applied update refreshed this item")
            superseded += 1
        graph.patch_object(item.id, patch)
        minted += 1
    graph.patch_object(delta.id, {
        "status": "applied", "resolved_by": actor,
    }, rationale=f"understanding delta applied by {actor}")
    return {"ok": True, "delta_id": delta.id, "items_minted": minted,
            "superseded": superseded, "skipped_unchanged": skipped_unchanged,
            "skipped_owner_edited": skipped_owner_edited}


def dismiss_understanding_delta_fn(
    graph, delta_ref: str, *, verdict: str = "dismiss", actor: str = "owner",
    reader=None,
) -> dict[str, Any]:
    """Dismiss or defer a delta — durable, and never a reopened review."""
    view = reader or graph
    delta = _delta_by_ref(view, delta_ref)
    if delta is None:
        return {"ok": False, "reason": "delta_not_found"}
    if verdict not in ("dismiss", "defer"):
        raise ValueError("verdict must be dismiss | defer")
    if delta.data.get("status") in ("applied", "dismissed", "superseded"):
        return {"ok": True, "already_resolved": True,
                "status": delta.data.get("status")}
    status = "dismissed" if verdict == "dismiss" else "deferred"
    graph.patch_object(delta.id, {
        "status": status, "resolved_by": actor,
    }, rationale=f"understanding delta {status} by {actor}")
    return {"ok": True, "delta_id": delta.id, "status": status}


def project_understanding_deltas_fn(
    reader, *, draft_id: str = "", subject_ref: str = "owner",
) -> list[dict[str, Any]]:
    rows = []
    for obj in reader.objects(type="understanding_delta"):
        data = obj.data or {}
        if draft_id and data.get("draft_id") != draft_id:
            continue
        if data.get("subject_ref") != subject_ref:
            continue
        rows.append({
            "id": obj.id,
            "delta_identity": data.get("delta_identity"),
            "draft_id": data.get("draft_id"),
            "version": int(data.get("version") or 0),
            "status": data.get("status"),
            "source": data.get("source"),
            "items": list(data.get("items") or []),
            "coverage": dict(data.get("coverage") or {}),
        })
    rows.sort(key=lambda row: row["version"])
    return rows


# ---- the zero-key floor -----------------------------------------------------

def draft_source_coverage_fn(reader, *, subject_ref: str = "owner") -> dict[str, Any]:
    """What fed (or failed to feed) this draft — per source: contributed,
    running, failed, or skipped, with the failure preserved. The owner sees
    honest coverage, never a pretend-complete draft (hardening Gate 3)."""
    def _row(status: str, detail: str = "", count: int = 0) -> dict[str, Any]:
        row: dict[str, Any] = {"status": status}
        if detail:
            row["detail"] = detail[:200]
        if count:
            row["count"] = count
        return row

    facts = [
        obj for obj in reader.objects(type="subject_fact")
        if obj.data.get("subject_ref") == subject_ref
        and obj.data.get("status") == "promoted"
    ]
    coverage: dict[str, Any] = {
        "identity_seed": _row("contributed" if facts else "skipped",
                              count=len(facts)),
    }

    research_plans = [
        obj for obj in reader.objects(type="connector_ingestion_plan")
        if obj.data.get("service") == "web_research"
    ]
    research_runs = [
        obj for obj in reader.objects(type="web_research_run")
    ]
    findings = sum(len(run.data.get("findings") or []) for run in research_runs)
    latest_plan = research_plans[-1] if research_plans else None
    plan_ref = str(latest_plan.data.get("plan_identity")) if latest_plan else ""
    if not research_plans:
        # A durable OPPORTUNITY, not a dead end: nothing was declined, the
        # source simply has not run (hardening round — the offer must
        # survive navigation, hatch, and restart).
        coverage["research"] = _row("available")
    elif any(p.data.get("status") in ("approved", "executing") for p in research_plans):
        coverage["research"] = {**_row("running"), "plan_ref": plan_ref}
    elif research_runs and all(
        run.data.get("status") == "failed" for run in research_runs
    ):
        coverage["research"] = {**_row(
            "failed", detail=str(research_runs[-1].data.get("error") or
                                 research_runs[-1].data.get("stop_reason") or ""),
        ), "plan_ref": plan_ref}
    elif any(p.data.get("status") == "fulfilled" for p in research_plans):
        coverage["research"] = _row("contributed" if findings else "empty",
                                    count=findings)
    elif latest_plan is not None and latest_plan.data.get("status") == "proposed":
        coverage["research"] = {**_row("proposed"), "plan_ref": plan_ref}
    else:
        coverage["research"] = _row("available")

    gmail_backfills = [
        obj for obj in reader.objects(type="connector_ingestion_plan")
        if obj.data.get("service") == "gmail"
        and obj.data.get("purpose") == "initial_backfill"
        and obj.data.get("status") == "fulfilled"
    ]
    sent_plans = [
        obj for obj in reader.objects(type="connector_ingestion_plan")
        if obj.data.get("service") == "gmail"
        and obj.data.get("purpose") == "comprehension"
        and obj.data.get("status") not in ("abandoned", "superseded")
    ]
    sent_plan = sent_plans[-1] if sent_plans else None
    sent_ref = str(sent_plan.data.get("plan_identity")) if sent_plan else ""
    comp_requests = list(reader.objects(type="comprehension_request"))
    leaves = len(list(reader.objects(type="source_item_summary")))
    if comp_requests and any(
        r.data.get("status") in ("proposed", "reducing", "aggregating")
        for r in comp_requests
    ):
        coverage["sent_mail"] = _row("running", count=leaves)
    elif comp_requests and any(
        r.data.get("status") == "completed" for r in comp_requests
    ):
        coverage["sent_mail"] = _row("contributed", count=leaves)
    elif comp_requests:
        coverage["sent_mail"] = _row(
            "failed", detail=str(comp_requests[-1].data.get("error") or ""),
        )
    elif sent_plan is not None and sent_plan.data.get("status") == "proposed":
        coverage["sent_mail"] = {**_row("proposed"), "plan_ref": sent_ref}
    elif sent_plan is not None:
        coverage["sent_mail"] = {**_row("running"), "plan_ref": sent_ref}
    elif gmail_backfills:
        # Mail is acquired and studyable — the opportunity stays open even
        # if the owner left the connection panel long ago.
        coverage["sent_mail"] = _row("available")
    else:
        coverage["sent_mail"] = _row("unavailable")
    return coverage


def _failed_draft_pass(reader) -> Optional[dict[str, Any]]:
    """The most recent failed keyed strong pass, if one preceded this
    composition — the fallback keeps the original failure inspectable."""
    failed = [
        obj for obj in reader.objects(type="subject_synthesis_request")
        if (obj.data.get("metadata") or {}).get("kind") == "setup_draft"
        and obj.data.get("status") == "failed"
    ]
    if not failed:
        return None
    last = failed[-1]
    return {
        "error": str(last.data.get("error") or "")[:200],
        "request_ref": last.id,
    }


def _deterministic_rows(view, *, subject_ref: str) -> list[dict[str, Any]]:
    """The deterministic floor's rows: pending candidates and
    connected-source hints, each carrying its candidate ref for reuse."""
    from packs.subject_profile.projection import classify_subject_attribute

    rows: list[dict[str, Any]] = []
    class_to_section = {
        "identity": "identity", "narrative": "narrative",
        "instruction": "instructions",
    }
    promoted_values = {
        (str(f.data.get("attribute") or ""), str(f.data.get("value") or ""))
        for f in view.objects(type="subject_fact")
        if f.data.get("subject_ref") == subject_ref
        and f.data.get("status") == "promoted"
    }
    for candidate in view.objects(type="profile_candidate"):
        data = candidate.data or {}
        if data.get("status") != "candidate":
            continue
        if (
            str(data.get("attribute") or ""), str(data.get("value") or "")
        ) in promoted_values:
            continue  # already confirmed: nothing to review again
        attribute = str(data.get("attribute") or "profile_statement")
        section = class_to_section[classify_subject_attribute(attribute)]
        value = str(data.get("value") or data.get("text") or "").strip()
        if not value:
            continue
        rows.append({
            "section": section,
            "proposed": {"attribute": attribute, "value": value},
            "rationale": str(data.get("text") or "pending from your review queue"),
            "evidence_refs": [ref for ref in [data.get("evidence_id")] if ref]
            or [candidate.id],
            "confidence": float(data.get("confidence") or 0.6),
            "uncertainty": "",
            "candidate_ref": candidate.id,
        })
    for candidate in view.objects(type="project_candidate"):
        data = candidate.data or {}
        if data.get("status") != "proposed":
            continue
        rows.append({
            "section": "projects",
            "proposed": {
                "name": str(data.get("name") or ""),
                "description": str(data.get("description") or "")[:800],
                "status_note": "", "people": [],
            },
            "rationale": str(data.get("rationale")
                             or "derived from your confirmed material"),
            "evidence_refs": list(data.get("sources") or [])[:10] or [candidate.id],
            "confidence": 0.6,
            "uncertainty": "",
            "candidate_ref": candidate.id,
        })
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
        rows.append({
            "section": "access",
            "proposed": {
                "question_class": f"questions about your {service} activity",
                "source": service,
                "strategy": f"search {service} {', '.join(surfaces)}",
            },
            "rationale": f"{service} is connected and its surfaces carry signal",
            "evidence_refs": [profile.id],
            "confidence": 0.6,
            "uncertainty": "",
            "candidate_ref": None,
        })
    return rows


def compose_deterministic_draft_fn(
    graph, *, subject_ref: str = "owner", reader=None
) -> dict[str, Any]:
    """The smaller deterministic draft (ADR 0046 §5): pending candidates and
    connected-source hints composed without a model, through the identical
    review path. Never a dead end — an empty draft is still resolvable.
    Against a frozen review snapshot, new material lands as an
    understanding delta (ADR 0048 §3), never a superseding version."""
    view = reader or graph
    rows = _deterministic_rows(view, subject_ref=subject_ref)
    fingerprint = synthesis_input_fingerprint_fn(view, subject_ref=subject_ref)

    head = current_setup_draft_fn(view, subject_ref=subject_ref)
    if (
        head is not None
        and head.data.get("status") == "proposed"
        and draft_review_started_fn(view, head)
    ):
        delta = _mint_understanding_delta(
            graph, view, head=head, rows=rows, source="deterministic",
            run_id=None, coverage={
                "composer": "deterministic_floor",
                "sources": draft_source_coverage_fn(view, subject_ref=subject_ref),
            },
        )
        graph.patch_object(head.id, {
            "coverage": {
                **dict(head.data.get("coverage") or {}),
                "input_fingerprint": fingerprint,
            },
        }, rationale="frozen head consumed this compose input horizon")
        return {**delta, "draft_id": None, "frozen_head": head.id,
                "source": "deterministic"}

    failed_pass = _failed_draft_pass(view)
    provenance = "deterministic_fallback" if failed_pass else "deterministic"
    draft = _mint_draft(
        graph, subject_ref=subject_ref, source="deterministic", run_id=None,
        included_refs=[], coverage={
            "composer": "deterministic_floor",
            "provenance": provenance,
            "sources": draft_source_coverage_fn(view, subject_ref=subject_ref),
            "input_fingerprint": fingerprint,
            **({"fallback_from": failed_pass} if failed_pass else {}),
        },
    )
    counts = {section: 0 for section in DRAFT_SECTIONS}
    for row in rows:
        _mint_item(
            graph, view, draft=draft, section=row["section"],
            proposed=dict(row["proposed"]),
            rationale=str(row["rationale"])[:500],
            evidence_refs=list(row["evidence_refs"]),
            confidence=float(row["confidence"]),
            uncertainty=str(row["uncertainty"]),
            candidate_ref=row.get("candidate_ref"), flags=[],
        )
        counts[row["section"]] += 1
    total = sum(counts.values())
    graph.patch_object(draft.id, {"counts": {"items": total, **counts}})
    return {"ok": True, "draft_id": draft.id, "items": total, "source": "deterministic"}


# ---- review -----------------------------------------------------------------

def review_setup_item_fn(
    graph, item_ref: str, verdict: str, *,
    actor: str = "owner", edited_value: Optional[dict[str, Any]] = None,
    correction: Optional[str] = None,
    reader=None,
) -> dict[str, Any]:
    """Owner verdict on one item. Accept/reject settles the pre-recorded
    prediction; edit supersedes the proposal as an owner declaration and is
    scored as its own verdict class — never as a correct 'accept'. A
    rejection may carry a typed correction reason (ADR 0048 §4) — the
    distinct teaching signal a bare 'no' throws away."""
    view = reader or graph
    item = view.get_object(item_ref) if hasattr(view, "get_object") else None
    if item is None or getattr(item, "type", None) != "setup_draft_item":
        return {"ok": False, "reason": "item_not_found"}
    if verdict not in ("accept", "reject", "edit", "defer"):
        raise ValueError("verdict must be accept | reject | edit | defer")
    if correction is not None and correction not in CORRECTION_REASONS:
        raise ValueError(
            f"correction must be one of {CORRECTION_REASONS}"
        )
    status = item.data.get("status")
    if status not in ("proposed", "accepted", "rejected", "edited", "deferred"):
        return {"ok": False, "reason": "item_already_committed", "status": status}
    patch: dict[str, Any] = {
        "verdict": verdict, "verdict_actor": actor,
        "status": {"accept": "accepted", "reject": "rejected",
                   "edit": "edited", "defer": "deferred"}[verdict],
    }
    if correction is not None:
        patch["correction"] = correction
    if verdict == "edit":
        if not isinstance(edited_value, dict) or not edited_value:
            raise ValueError("edit requires edited_value")
        merged = {**dict(item.data.get("proposed") or {}), **edited_value}
        patch["edited_value"] = merged
    graph.patch_object(item.id, patch, rationale=f"setup item {verdict} by {actor}")
    return {"ok": True, "item_id": item.id, "status": patch["status"]}


def comment_setup_item_fn(
    graph, item_ref: str, text: str, *, actor: str = "owner", reader=None,
) -> dict[str, Any]:
    """An owner comment on one item: durable owner evidence that starts the
    review freeze like any decision, changes no verdict, and never counts
    as a correct system prediction (ADR 0048 §4)."""
    view = reader or graph
    item = view.get_object(item_ref) if hasattr(view, "get_object") else None
    if item is None or getattr(item, "type", None) != "setup_draft_item":
        return {"ok": False, "reason": "item_not_found"}
    cleaned, _ = _sanitize(text, 500)
    if not cleaned.strip():
        raise ValueError("a comment needs text")
    comments = list(item.data.get("comments") or [])
    comments.append({
        "text": cleaned, "actor": actor, "sequence": len(comments) + 1,
    })
    graph.patch_object(item.id, {"comments": comments[-12:]},
                       rationale=f"owner comment recorded by {actor}")
    return {"ok": True, "item_id": item.id, "comments": len(comments)}


def split_setup_project_item_fn(
    graph, item_ref: str, parts: list[dict[str, Any]], *,
    actor: str = "owner", reader=None,
) -> dict[str, Any]:
    """Split one project item into N fresh proposals (ADR 0048 §4): the
    original is superseded, each part gets its own candidate and review —
    no verdict carries onto content the owner just reshaped."""
    view = reader or graph
    item = view.get_object(item_ref) if hasattr(view, "get_object") else None
    if item is None or getattr(item, "type", None) != "setup_draft_item":
        return {"ok": False, "reason": "item_not_found"}
    if item.data.get("section") != "projects":
        return {"ok": False, "reason": "not_a_project_item"}
    if item.data.get("status") in ("committed", "commit_failed", "superseded"):
        return {"ok": False, "reason": "item_already_committed"}
    cleaned_parts = []
    for part in parts or []:
        name = " ".join(str((part or {}).get("name") or "").split())
        if name:
            description, _ = _sanitize((part or {}).get("description") or "", 800)
            cleaned_parts.append({"name": name, "description": description})
    if len(cleaned_parts) < 2:
        raise ValueError("splitting needs at least two named parts")
    draft = _draft_by_ref(view, str(item.data.get("draft_id") or ""))
    if draft is None:
        return {"ok": False, "reason": "draft_not_found"}
    subject_ref = str(draft.data.get("subject_ref") or "owner")
    refs = list(item.data.get("evidence_refs") or [])
    created = []
    for part in cleaned_parts:
        candidate_ref = _ensure_project_candidate(
            graph, view, subject_ref=subject_ref,
            name=part["name"], description=part["description"],
            refs=refs, rationale=f"split from a broader proposal by {actor}",
        )
        minted = _mint_item(
            graph, view, draft=draft, section="projects",
            proposed={"name": part["name"], "description": part["description"],
                      "status_note": "", "people": []},
            rationale=f"split by {actor} from a broader proposal",
            evidence_refs=refs or [item.id],
            confidence=float(item.data.get("confidence") or 0.6),
            uncertainty="",
            candidate_ref=candidate_ref, flags=[],
        )
        graph.patch_object(minted.id, {
            "metadata": {**dict(minted.data.get("metadata") or {}),
                         "split_from": item.id},
        })
        created.append(minted.id)
    graph.patch_object(item.id, {
        "status": "superseded", "verdict": "edit", "verdict_actor": actor,
        "metadata": {**dict(item.data.get("metadata") or {}),
                     "split_into": created},
    }, rationale=f"split into {len(created)} items by {actor}")
    return {"ok": True, "item_id": item.id, "created": len(created),
            "item_ids": created}


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


def review_setup_items_fn(
    graph, item_refs: list[str], verdict: str, *,
    actor: str = "owner", correction: Optional[str] = None, reader=None,
) -> dict[str, Any]:
    """One explicit batch verdict over N items — the category-level decision
    (ADR 0048 §4 amendment). Atomic at the host-call level: every item
    settles inside one engine call instead of a fragile chain of client
    commands; per-item results are returned so a refused item is visible,
    not silently skipped."""
    results = []
    settled = 0
    for ref in item_refs[:120]:
        result = review_setup_item_fn(
            graph, str(ref), verdict, actor=actor, correction=correction,
            reader=reader,
        )
        results.append({"item_ref": str(ref), **result})
        if result.get("ok"):
            settled += 1
    return {"ok": settled == len(results), "settled": settled,
            "total": len(results), "results": results}


#: Tokens too generic to signal project-name overlap on their own.
_OVERLAP_STOPWORDS = frozenset((
    "the", "a", "an", "of", "and", "for", "with", "inc", "llc", "co",
    "company", "fund", "i", "ii", "iii", "operations", "ops", "project",
    "projects", "initiative", "venture", "ventures", "vc", "capital",
    "management", "team", "work", "app", "ai",
))


def _overlap_signature(name: str) -> set[str]:
    tokens = {
        token for token in "".join(
            ch if ch.isalnum() else " " for ch in name.casefold()
        ).split()
        if len(token) >= 3 and token not in _OVERLAP_STOPWORDS
    }
    return tokens


def possible_overlap_clusters_fn(
    reader, *, draft_id: str = "", subject_ref: str = "owner",
) -> list[dict[str, Any]]:
    """Deterministic possible-overlap clusters over the ACTIVE project items
    of the head draft: naming variants that share a significant token (or a
    name-prefix) may be one workstream or two — the owner decides. Never an
    auto-merge; a dismissed cluster stays dismissed (draft metadata)."""
    if draft_id:
        head = _draft_by_ref(reader, draft_id)
    else:
        head = current_setup_draft_fn(reader, subject_ref=subject_ref)
    if head is None:
        return []
    dismissed = set(
        (head.data.get("metadata") or {}).get("overlap_dismissed") or []
    )
    items = [
        item for item in _items_for_draft(reader, head.id)
        if item.data.get("section") == "projects"
        and item.data.get("status") not in ("superseded", "committed")
    ]
    rows = []
    for item in items:
        value = dict(item.data.get("edited_value") or item.data.get("proposed") or {})
        name = str(value.get("name") or "")
        rows.append({"item": item, "name": name,
                     "signature": _overlap_signature(name),
                     "normalized": " ".join(name.casefold().split())})
    parent = list(range(len(rows)))

    def _find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    shared: dict[tuple[int, int], set[str]] = {}
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            tokens = rows[i]["signature"] & rows[j]["signature"]
            prefix = (
                rows[i]["normalized"] and rows[j]["normalized"]
                and (rows[i]["normalized"].startswith(rows[j]["normalized"])
                     or rows[j]["normalized"].startswith(rows[i]["normalized"]))
            )
            if tokens or prefix:
                shared[(i, j)] = tokens
                ri, rj = _find(i), _find(j)
                if ri != rj:
                    parent[rj] = ri
    groups: dict[int, list[int]] = {}
    for index in range(len(rows)):
        groups.setdefault(_find(index), []).append(index)
    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        member_ids = sorted(rows[index]["item"].id for index in members)
        cluster_key = _stable("overlap", head.id, *member_ids)[:24]
        if cluster_key in dismissed:
            continue
        tokens = sorted({
            token
            for (i, j), toks in shared.items()
            if i in members and j in members
            for token in toks
        })
        clusters.append({
            "cluster_key": cluster_key,
            "items": [
                {
                    "item_id": rows[index]["item"].id,
                    "name": rows[index]["name"],
                    "status": rows[index]["item"].data.get("status"),
                }
                for index in sorted(members)
            ],
            "shared_tokens": tokens,
            "why": (
                "these project names share "
                + (f"the token(s) {', '.join(tokens)}" if tokens
                   else "a common name prefix")
                + " — they may be one workstream or genuinely distinct"
            ),
        })
    clusters.sort(key=lambda row: row["cluster_key"])
    return clusters


def dismiss_overlap_cluster_fn(
    graph, draft_ref: str, cluster_key: str, *, actor: str = "owner",
    reader=None,
) -> dict[str, Any]:
    """'Keep separate' — durable: the cluster never re-flags for this
    snapshot."""
    view = reader or graph
    draft = _draft_by_ref(view, draft_ref)
    if draft is None:
        return {"ok": False, "reason": "draft_not_found"}
    metadata = dict(draft.data.get("metadata") or {})
    dismissed = list(metadata.get("overlap_dismissed") or [])
    if cluster_key not in dismissed:
        dismissed.append(cluster_key)
    metadata["overlap_dismissed"] = dismissed[-40:]
    graph.patch_object(draft.id, {"metadata": metadata},
                       rationale=f"overlap cluster kept separate by {actor}")
    return {"ok": True, "draft_id": draft.id, "cluster_key": cluster_key}


# ---- submission -------------------------------------------------------------

def _settle_campaign_at_resolution(graph, view) -> None:
    """A resolved review is the campaign's terminal moment (ADR 0047 §1):
    any non-terminal campaign — open OR parked on a pause — settles
    completed instead of lingering as an ownerless paused ledger."""
    try:
        from .coordinator import current_campaign_fn, settle_campaign_fn

        campaign = current_campaign_fn(view)
        if campaign is not None and campaign.data.get("status") in (
            "open", "paused_owner",
        ):
            settle_campaign_fn(graph, campaign.id, status="completed",
                               stop_reason="review_ready")
    except Exception:
        pass  # campaign settlement must never block a review resolution


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
    _settle_campaign_at_resolution(graph, view)
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
    _settle_campaign_at_resolution(graph, view)
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
        return {"draft": None, "items": [], "resolved": False,
                "review_started": False, "deltas": []}
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
            "correction": item.data.get("correction"),
            "comments": list(item.data.get("comments") or []),
            "candidate_ref": item.data.get("candidate_ref"),
            "semantic_key": semantic_item_key(
                str(item.data.get("section") or ""),
                dict(item.data.get("edited_value")
                     or item.data.get("proposed") or {}),
            ),
            "delta_ref": (item.data.get("metadata") or {}).get("delta_ref"),
            "split_from": (item.data.get("metadata") or {}).get("split_from"),
            "predicted_verdict": item.data.get("predicted_verdict"),
            "predicted_confidence_percent": item.data.get("predicted_confidence_percent"),
            "injection_flags": item.data.get("injection_flags") or [],
            "commit_error": item.data.get("commit_error"),
        }
        for item in _items_for_draft(reader, draft.id)
        # Superseded rows are history (merged away, refreshed by an applied
        # update): the review renders one active item per semantic key.
        if item.data.get("status") != "superseded"
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
        "review_started": draft_review_started_fn(reader, draft),
        "deltas": project_understanding_deltas_fn(
            reader, draft_id=draft.id, subject_ref=subject_ref,
        ),
    }


__all__ = [
    "CORRECTION_REASONS",
    "DRAFT_SECTIONS",
    "SECTION_DESTINATIONS",
    "apply_understanding_delta_fn",
    "begin_setup_draft_submission_fn",
    "begin_setup_review_fn",
    "comment_setup_item_fn",
    "commit_setup_draft_fn",
    "compose_deterministic_draft_fn",
    "consumed_input_fingerprint_fn",
    "current_setup_draft_fn",
    "defer_setup_draft_fn",
    "dismiss_overlap_cluster_fn",
    "dismiss_understanding_delta_fn",
    "draft_review_started_fn",
    "merge_setup_project_items_fn",
    "perform_setup_draft",
    "possible_overlap_clusters_fn",
    "prepare_setup_draft_fn",
    "project_setup_draft_fn",
    "project_understanding_deltas_fn",
    "reclassify_setup_item_fn",
    "request_setup_draft_fn",
    "resubmit_setup_draft_fn",
    "review_setup_item_fn",
    "review_setup_items_fn",
    "semantic_item_key",
    "split_setup_project_item_fn",
    "synthesis_input_fingerprint_fn",
]
