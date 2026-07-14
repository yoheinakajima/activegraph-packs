"""Source lenses and the shared working understanding (ADR 0047 §3–4).

Each selected source contributes through a versioned lens whose rows keep
two kinds of lineage: ``support_refs`` — evidence the source itself
contains — and ``context_refs`` — borrowed other-source material that
helped selection or interpretation. Corroboration counts independent
support lineage only, so feeding a web hypothesis into mail analysis and
reading it back can never strengthen the hypothesis by itself.

The working understanding is one non-canonical versioned snapshot composed
deterministically from promoted facts, lens contributions, candidates, and
unresolved questions. A material change schedules targeted, version-pinned
reinterpretation of the affected downstream steps — never a silent rewrite
and never a global rerun.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

WORKING_COMPOSER = "subject_synthesis.working@0.1.0"

#: Authority/use classes (ADR 0047 §4), strongest first.
AUTHORITY_CLASSES = (
    "owner_confirmed",   # promoted/owner-declared: may guide approved outward queries
    "hypothesis",        # source-supported: local interpretation and owner questions only
    "unresolved",        # ambiguous: compare/show only
    "denied",            # withdrawn: excluded from active coordination
)

ENTRY_KINDS = (
    "fact",          # promoted owner material
    "hypothesis",    # source-backed candidate claim
    "alias",         # known alias/handle/name variant
    "entity",        # person/org/product the owner's world contains
    "relationship",  # proposed relationship between entities
    "question",      # unresolved question worth information gain
    "conflict",      # contradiction between sources
    "organization",  # work-organization/view candidate
)


def _stable(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()}"


def _lens_by_key(reader, affordance_id: str, source_surface_id: str):
    return next(
        (obj for obj in reader.objects(type="source_lens")
         if obj.data.get("affordance_id") == affordance_id
         and obj.data.get("source_surface_id") == source_surface_id),
        None,
    )


def _sanitize_text(value: str, limit: int = 300) -> tuple[str, list[str]]:
    from packs.tool_gateway.sanitizer import sanitize_output
    from packs.tool_gateway.untrusted import scan_for_injection

    cleaned, _ = sanitize_output(str(value)[:limit])
    return cleaned, scan_for_injection(cleaned)


def ensure_source_lens_fn(
    graph, *, affordance_id: str, source_surface_id: str,
    service: str = "", subject_ref: str = "owner", reader=None,
) -> dict[str, Any]:
    """Open (or return) the one lens for (affordance, surface)."""
    view = reader or graph
    existing = _lens_by_key(view, affordance_id, source_surface_id)
    if existing is not None:
        return {"ok": True, "lens_id": existing.id, "created": False}
    lens = graph.add_object("source_lens", {
        "lens_identity": _stable("source_lens", affordance_id, source_surface_id),
        "affordance_id": affordance_id,
        "service": service,
        "source_surface_id": source_surface_id,
        "subject_ref": subject_ref,
        "status": "pending",
        "working_version_pinned": 0,
        "contribution_count": 0,
        "contributions": [],
        "coverage": {},
        "gaps": [],
        "exclusions": {},
        "uncertainties": [],
        "terminal_reason": "",
        "metadata": {},
    })
    return {"ok": True, "lens_id": lens.id, "created": True}


def contribute_source_lens_fn(
    graph, *, affordance_id: str, source_surface_id: str,
    contributions: list[dict[str, Any]],
    coverage: Optional[dict[str, Any]] = None,
    gaps: Optional[list[str]] = None,
    exclusions: Optional[dict[str, Any]] = None,
    uncertainties: Optional[list[str]] = None,
    working_version_read: int = 0,
    terminal: Optional[str] = None,
    terminal_reason: str = "",
    service: str = "",
    reader=None,
) -> dict[str, Any]:
    """Record one lens contribution (ADR 0047 §3).

    Every row must separate ``support_refs`` from ``context_refs``; a row
    claiming support it does not carry refs for is dropped and counted. The
    lens pins the working-understanding version it read so reinterpretation
    stays targeted and replayable.
    """
    view = reader or graph
    ensure_source_lens_fn(
        graph, affordance_id=affordance_id, source_surface_id=source_surface_id,
        service=service, reader=reader,
    )
    lens = _lens_by_key(view, affordance_id, source_surface_id)
    if lens is None:  # freshly created this call — re-read through the graph
        lens = _lens_by_key(graph, affordance_id, source_surface_id)
    data = dict(lens.data or {})
    kept: list[dict[str, Any]] = []
    dropped_unsupported = 0
    flags: list[str] = []
    for row in contributions or []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "hypothesis")
        if kind not in ENTRY_KINDS:
            kind = "hypothesis"
        statement, row_flags = _sanitize_text(row.get("statement") or "", 300)
        flags.extend(row_flags)
        support = [str(r) for r in (row.get("support_refs") or []) if str(r)]
        context = [str(r) for r in (row.get("context_refs") or []) if str(r)]
        if not statement:
            continue
        if not support and kind not in ("question", "conflict"):
            # A contribution with no evidence of its own is not a
            # contribution — at best it is repeating borrowed context.
            dropped_unsupported += 1
            continue
        kept.append({
            "entry_key": str(row.get("entry_key") or "").strip()
            or _stable("entry", kind, statement.casefold())[:40],
            "kind": kind,
            "statement": statement,
            "support_refs": support[:10],
            "context_refs": context[:10],
            "confidence": min(1.0, max(0.0, float(row.get("confidence") or 0.5))),
            "attributes": dict(row.get("attributes") or {}),
        })
    merged = list(data.get("contributions") or [])
    known = {row.get("entry_key") for row in merged}
    appended = 0
    for row in kept:
        if row["entry_key"] in known:
            continue
        merged.append(row)
        known.add(row["entry_key"])
        appended += 1
    status = data.get("status") or "pending"
    if terminal:
        if terminal not in ("contributed", "failed", "declined", "unavailable"):
            raise ValueError(
                "terminal must be contributed | failed | declined | unavailable"
            )
        status = terminal
    elif appended or merged:
        status = "contributing"
    patch = {
        "status": status,
        "working_version_pinned": max(
            int(data.get("working_version_pinned") or 0),
            int(working_version_read or 0),
        ),
        "contribution_count": len(merged),
        "contributions": merged[-200:],
        "coverage": {**dict(data.get("coverage") or {}), **dict(coverage or {})},
        "gaps": list(dict.fromkeys([*(data.get("gaps") or []), *(gaps or [])]))[:40],
        "exclusions": {**dict(data.get("exclusions") or {}), **dict(exclusions or {})},
        "uncertainties": list(dict.fromkeys(
            [*(data.get("uncertainties") or []), *(uncertainties or [])]
        ))[:40],
        "terminal_reason": terminal_reason[:200] if terminal else str(
            data.get("terminal_reason") or ""
        ),
    }
    if flags:
        patch["metadata"] = {
            **dict(data.get("metadata") or {}),
            "injection_flags": sorted(set(
                [*((data.get("metadata") or {}).get("injection_flags") or []), *flags]
            )),
        }
    graph.patch_object(lens.id, patch, rationale="source lens contribution")
    return {
        "ok": True, "lens_id": lens.id, "appended": appended,
        "dropped_unsupported": dropped_unsupported, "status": status,
    }


def settle_source_lens_fn(
    graph, *, affordance_id: str, source_surface_id: str,
    terminal: str, reason: str = "", reader=None,
) -> dict[str, Any]:
    """Settle a lens terminally (contributed/failed/declined/unavailable) —
    optional sources settle honestly instead of dead-ending the journey."""
    return contribute_source_lens_fn(
        graph, affordance_id=affordance_id, source_surface_id=source_surface_id,
        contributions=[], terminal=terminal, terminal_reason=reason,
        reader=reader,
    )


# ---- corroboration ----------------------------------------------------------

def _support_sources_for_statement(
    lenses: list[Any], statement_key: str
) -> set[str]:
    """The distinct affordances whose OWN support refs back this statement.
    Context refs never count (ADR 0047 §3): borrowed understanding is not
    independent corroboration."""
    sources: set[str] = set()
    for lens in lenses:
        for row in lens.data.get("contributions") or []:
            if str(row.get("statement") or "").casefold() != statement_key:
                continue
            if row.get("support_refs"):
                sources.add(str(lens.data.get("affordance_id") or ""))
    return {s for s in sources if s}


# ---- the working understanding ----------------------------------------------

def _entry(
    kind: str, statement: str, authority: str, *,
    support: Optional[list[dict[str, Any]]] = None,
    context_refs: Optional[list[str]] = None,
    confidence: float = 0.5,
    corroboration: int = 0,
    attributes: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "entry_id": _stable("working_entry", kind, statement.casefold())[:48],
        "kind": kind,
        "statement": statement[:300],
        "authority": authority,
        "support": support or [],
        "context_refs": (context_refs or [])[:10],
        "confidence": round(min(1.0, max(0.0, confidence)), 3),
        "corroboration": int(corroboration),
        "attributes": attributes or {},
    }


def current_working_understanding_fn(reader, *, subject_ref: str = "owner"):
    rows = [
        obj for obj in reader.objects(type="working_understanding")
        if obj.data.get("subject_ref") == subject_ref
    ]
    if not rows:
        return None
    rows.sort(key=lambda obj: int(obj.data.get("version") or 0))
    return rows[-1]


def compose_working_understanding_fn(
    graph, *, subject_ref: str = "owner", reader=None,
    pins: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Compose (or refresh) the working understanding deterministically.

    Sources, in authority order: promoted subject facts (owner-confirmed);
    denied/rejected verdict history (denied); lens contributions
    (hypotheses with counted independent corroboration); unresolved owner
    questions and conflicts. A new version is minted only when the content
    hash moves; the material change list drives targeted reinterpretation.
    """
    view = reader or graph
    entries: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    denied_values: set[str] = set()
    for candidate in view.objects(type="profile_candidate"):
        data = candidate.data or {}
        if data.get("status") == "rejected":
            key = str(data.get("value") or data.get("text") or "").casefold()
            if key:
                denied_values.add(key)

    seen_statements: set[str] = set()
    for fact in view.objects(type="subject_fact"):
        data = fact.data or {}
        if data.get("subject_ref") != subject_ref:
            continue
        if data.get("status") == "forgotten":
            denied_values.add(str(data.get("value") or "").casefold())
            continue
        if data.get("status") != "promoted":
            continue
        statement = f"{data.get('attribute')}: {data.get('value')}"
        key = statement.casefold()
        if key in seen_statements:
            continue
        seen_statements.add(key)
        entries.append(_entry(
            "fact", statement, "owner_confirmed",
            support=[{"source": "owner", "refs": [fact.id]}],
            confidence=1.0,
            attributes={
                "attribute": str(data.get("attribute") or ""),
                "value": str(data.get("value") or ""),
            },
        ))

    lenses = list(view.objects(type="source_lens"))
    lens_statements: dict[str, dict[str, Any]] = {}
    for lens in lenses:
        affordance_id = str(lens.data.get("affordance_id") or "")
        for row in lens.data.get("contributions") or []:
            statement = str(row.get("statement") or "")
            key = statement.casefold()
            if not statement or key in seen_statements:
                continue
            if key in denied_values:
                continue
            bucket = lens_statements.setdefault(key, {
                "statement": statement,
                "kind": str(row.get("kind") or "hypothesis"),
                "support": [],
                "context_refs": [],
                "confidence": 0.0,
                "attributes": dict(row.get("attributes") or {}),
            })
            if row.get("support_refs"):
                bucket["support"].append({
                    "source": affordance_id,
                    "refs": list(row.get("support_refs") or [])[:6],
                })
            bucket["context_refs"] = list(dict.fromkeys(
                [*bucket["context_refs"], *(row.get("context_refs") or [])]
            ))[:10]
            bucket["confidence"] = max(
                bucket["confidence"], float(row.get("confidence") or 0.5)
            )
    for key, bucket in sorted(lens_statements.items()):
        supporting_sources = {row["source"] for row in bucket["support"]}
        corroboration = len(supporting_sources)
        if corroboration == 0:
            # Context-only repetition: it exists, but as an unresolved echo,
            # never as a supported hypothesis (the anti-laundering rule).
            unresolved.append({
                "kind": "uncorroborated_echo",
                "statement": bucket["statement"][:300],
                "context_refs": bucket["context_refs"],
            })
            continue
        kind = bucket["kind"] if bucket["kind"] in ENTRY_KINDS else "hypothesis"
        if kind == "question":
            unresolved.append({
                "kind": "question",
                "statement": bucket["statement"][:300],
                "support": bucket["support"],
            })
            continue
        if kind == "conflict":
            unresolved.append({
                "kind": "conflict",
                "statement": bucket["statement"][:300],
                "support": bucket["support"],
            })
            continue
        # Confidence grows only with independent support lineage.
        confidence = min(0.95, bucket["confidence"] * (0.6 + 0.2 * corroboration))
        entries.append(_entry(
            kind if kind != "fact" else "hypothesis",
            bucket["statement"], "hypothesis",
            support=bucket["support"],
            context_refs=bucket["context_refs"],
            confidence=confidence,
            corroboration=corroboration,
            attributes=bucket["attributes"],
        ))

    for question in view.objects(type="owner_question"):
        data = question.data or {}
        if data.get("status") == "open":
            unresolved.append({
                "kind": "owner_question",
                "statement": str(data.get("prompt") or "")[:300],
                "question_ref": question.id,
                "question_kind": str(data.get("kind") or ""),
            })
        elif data.get("status") == "answered":
            # An answered campaign question IS an owner declaration: it may
            # guide approved outward scope (ADR 0047 §4/§5).
            answer = dict(data.get("answer") or {})
            answered = str(answer.get("label") or answer.get("text") or "")
            if answered:
                statement = f"owner answered: {answered}"
                key = statement.casefold()
                if key not in seen_statements:
                    seen_statements.add(key)
                    entries.append(_entry(
                        "fact", statement, "owner_confirmed",
                        support=[{"source": "owner", "refs": [question.id]}],
                        confidence=1.0,
                        attributes={"question_kind": str(data.get("kind") or "")},
                    ))

    for candidate in view.objects(type="project_candidate"):
        data = candidate.data or {}
        if data.get("status") != "proposed":
            continue
        entries.append(_entry(
            "organization", f"workstream candidate: {data.get('name')}",
            "hypothesis",
            support=[{"source": "projects", "refs": list(data.get("sources") or [])[:6]}],
            confidence=min(0.9, int(data.get("score_milli") or 0) / 1000),
            attributes={"candidate_ref": candidate.id},
        ))

    source_coverage: dict[str, Any] = {}
    for lens in lenses:
        source_coverage[str(lens.data.get("affordance_id") or lens.id)] = {
            "surface": lens.data.get("source_surface_id"),
            "status": lens.data.get("status"),
            "contributions": int(lens.data.get("contribution_count") or 0),
            "pinned_version": int(lens.data.get("working_version_pinned") or 0),
            "gaps": list(lens.data.get("gaps") or [])[:8],
            "terminal_reason": lens.data.get("terminal_reason") or "",
        }

    entries.sort(key=lambda row: (row["kind"], row["statement"].casefold()))
    unresolved.sort(key=lambda row: (row["kind"], str(row.get("statement") or "")))
    # Coverage changes (a lens settling, a gap closing) are material to the
    # packet the coordinator reads, so they version the snapshot — but only
    # ENTRY-kind changes schedule reinterpretation below.
    content_hash = hashlib.sha256(json.dumps(
        {"entries": entries, "unresolved": unresolved,
         "source_coverage": source_coverage},
        sort_keys=True, ensure_ascii=False,
    ).encode("utf-8")).hexdigest()

    head = current_working_understanding_fn(view, subject_ref=subject_ref)
    if head is not None and head.data.get("content_hash") == content_hash:
        return {
            "ok": True, "working_id": head.id, "created": False,
            "version": int(head.data.get("version") or 1),
        }

    prior_entries = {
        row.get("entry_id"): row for row in (head.data.get("entries") if head else []) or []
    }
    changed_kinds = sorted({
        row["kind"] for row in entries
        if row["entry_id"] not in prior_entries
        or prior_entries[row["entry_id"]] != row
    } | {
        row.get("kind") for eid, row in prior_entries.items()
        if eid not in {r["entry_id"] for r in entries}
    })
    version = 1 if head is None else int(head.data.get("version") or 0) + 1
    working = graph.add_object("working_understanding", {
        "working_identity": _stable("working", subject_ref, version),
        "version": version,
        "subject_ref": subject_ref,
        "entries": entries[:200],
        "unresolved": unresolved[:60],
        "source_coverage": source_coverage,
        "organization_candidates": [
            row for row in entries if row["kind"] == "organization"
        ][:24],
        "pins": {"composer": WORKING_COMPOSER, **(pins or {})},
        "predecessor_ref": head.id if head is not None else None,
        "content_hash": content_hash,
        "changed_kinds": [k for k in changed_kinds if k],
        "metadata": {},
    })
    _schedule_targeted_reinterpretation(
        graph, view, working_version=version,
        changed_kinds=[k for k in changed_kinds if k], lenses=lenses,
    )
    return {
        "ok": True, "working_id": working.id, "created": True,
        "version": version, "changed_kinds": [k for k in changed_kinds if k],
    }


#: Which downstream step a material change affects (ADR 0047 §3). This map
#: is deliberately narrow: a coverage-only change reschedules nothing, and
#: no change reruns a source's acquisition.
_REINTERPRETATION_TARGETS = {
    "fact": ("synthesis",),
    "hypothesis": ("synthesis",),
    "entity": ("lens_alignment", "synthesis"),
    "alias": ("lens_alignment",),
    "relationship": ("synthesis",),
    "organization": ("synthesis",),
    "question": (),
    "conflict": (),
}


def _schedule_targeted_reinterpretation(
    graph, view, *, working_version: int, changed_kinds: list[str],
    lenses: list[Any],
) -> None:
    if working_version <= 1:
        return  # the first composition reinterprets nothing
    targets: set[tuple[str, str]] = set()
    for kind in changed_kinds:
        for target_kind in _REINTERPRETATION_TARGETS.get(kind, ()):
            if target_kind == "lens_alignment":
                for lens in lenses:
                    if lens.data.get("status") in ("contributing", "contributed") and (
                        int(lens.data.get("working_version_pinned") or 0)
                        < working_version
                    ):
                        targets.add(("lens_alignment", lens.id))
            else:
                targets.add((target_kind, ""))
    existing = {
        (obj.data.get("target_kind"), obj.data.get("target_ref"))
        for obj in view.objects(type="reinterpretation_request")
        if obj.data.get("status") == "proposed"
    }
    for target_kind, target_ref in sorted(targets):
        if (target_kind, target_ref) in existing:
            continue
        graph.add_object("reinterpretation_request", {
            "request_identity": _stable(
                "reinterpret", working_version, target_kind, target_ref
            ),
            "working_version": working_version,
            "target_kind": target_kind,
            "target_ref": target_ref,
            "reason": f"working understanding v{working_version} changed: "
                      f"{', '.join(changed_kinds)}"[:200],
            "status": "proposed",
            "successor_ref": None,
            "error": None,
            "metadata": {},
        })


def pending_reinterpretations_fn(reader) -> list[dict[str, Any]]:
    rows = [
        {
            "request_ref": obj.id,
            "target_kind": str(obj.data.get("target_kind") or ""),
            "target_ref": str(obj.data.get("target_ref") or ""),
            "working_version": int(obj.data.get("working_version") or 0),
        }
        for obj in reader.objects(type="reinterpretation_request")
        if obj.data.get("status") == "proposed"
    ]
    rows.sort(key=lambda row: (row["working_version"], row["target_kind"], row["target_ref"]))
    return rows


def settle_reinterpretation_fn(
    graph, request_ref: str, *, status: str, successor_ref: str = "",
    error: str = "", reader=None,
) -> dict[str, Any]:
    view = reader or graph
    request = view.get_object(request_ref) if hasattr(view, "get_object") else None
    if request is None or getattr(request, "type", None) != "reinterpretation_request":
        return {"ok": False, "reason": "request_not_found"}
    if status not in ("completed", "failed", "skipped"):
        raise ValueError("status must be completed | failed | skipped")
    graph.patch_object(request.id, {
        "status": status,
        "successor_ref": successor_ref or None,
        "error": error[:300] or None,
    }, rationale="reinterpretation settled")
    return {"ok": True, "request_id": request.id, "status": status}


def project_working_understanding_fn(
    reader, *, subject_ref: str = "owner"
) -> dict[str, Any]:
    """The bounded packet coordinators and clients read."""
    head = current_working_understanding_fn(reader, subject_ref=subject_ref)
    if head is None:
        return {
            "exists": False, "version": 0, "entries": [], "unresolved": [],
            "source_coverage": {}, "organization_candidates": [],
        }
    data = head.data or {}
    return {
        "exists": True,
        "working_id": head.id,
        "version": int(data.get("version") or 0),
        "entries": list(data.get("entries") or []),
        "unresolved": list(data.get("unresolved") or []),
        "source_coverage": dict(data.get("source_coverage") or {}),
        "organization_candidates": list(data.get("organization_candidates") or []),
        "changed_kinds": list(data.get("changed_kinds") or []),
        "predecessor_ref": data.get("predecessor_ref"),
    }


__all__ = [
    "AUTHORITY_CLASSES",
    "ENTRY_KINDS",
    "WORKING_COMPOSER",
    "compose_working_understanding_fn",
    "contribute_source_lens_fn",
    "current_working_understanding_fn",
    "ensure_source_lens_fn",
    "pending_reinterpretations_fn",
    "project_working_understanding_fn",
    "settle_reinterpretation_fn",
    "settle_source_lens_fn",
]
