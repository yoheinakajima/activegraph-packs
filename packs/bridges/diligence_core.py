"""Diligence → Core Bridge — v0.1.

Subscribes to Diligence pack object.created events and creates parallel
Core primitive objects so Diligence outputs appear alongside outputs from
all other packs in the shared graph.

Mapping
-------
  diligence.document  → core.source       (kind=document, content=summary, url=url)
  diligence.claim     → core.observation  (text=text, confidence=confidence)
  diligence.memo      → core.artifact     (kind=memo, content=summary)
  diligence.risk      → core.evaluation   (judgment=severity, rationale=description)

All Core objects carry a `derived_from` relation back to their Diligence origin.

The bridge does NOT modify the Diligence pack.  It only reads Diligence
events and emits Core equivalents.

Registries
----------
  _BRIDGE_SEEN: diligence_object_id → core_object_id
  Call clear_bridge_registry() between test fixtures.
"""

from __future__ import annotations

from typing import Any

from activegraph.packs import behavior


_BRIDGE_SEEN: dict[str, str] = {}


def clear_bridge_registry() -> None:
    _BRIDGE_SEEN.clear()


# ──────────────────────────────────────────────────────────────────────────────
# document  →  source
# ──────────────────────────────────────────────────────────────────────────────

@behavior(
    name="document_to_source",
    on=["object.created"],
    where={"object.type": "document"},
    creates=["source"],
)
def document_to_source(event, graph, ctx, *, settings=None):
    """Map a Diligence document to a Core source.

    Diligence document fields: title, url, company_id, summary, published_at
    Core source fields: kind, content, url, metadata
    """
    obj = event.payload.get("object", {})
    doc_id = obj.get("id")
    if not doc_id or doc_id in _BRIDGE_SEEN:
        return

    data = obj.get("data", {})
    title = data.get("title") or "Untitled Document"
    url = data.get("url")
    summary = data.get("summary") or ""
    company_id = data.get("company_id")
    published_at = data.get("published_at")

    try:
        src = graph.add_object("source", {
            "kind": "document",
            "content": summary,
            "url": url,
            "metadata": {
                "title": title,
                "company_id": company_id,
                "published_at": published_at,
                "diligence_document_id": doc_id,
                "bridge": "diligence_core",
            },
        })
        _BRIDGE_SEEN[doc_id] = src.id
        try:
            graph.add_relation(src.id, doc_id, "derived_from")
        except Exception:
            pass
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# claim  →  observation
# ──────────────────────────────────────────────────────────────────────────────

@behavior(
    name="claim_to_observation",
    on=["object.created"],
    where={"object.type": "claim"},
    creates=["observation"],
)
def claim_to_observation(event, graph, ctx, *, settings=None):
    """Map a Diligence claim to a Core observation.

    Diligence claim fields: text, confidence, company_id, source_document_id, status
    Core observation fields: text, confidence, category, source_ids, metadata
    """
    obj = event.payload.get("object", {})
    claim_id = obj.get("id")
    if not claim_id or claim_id in _BRIDGE_SEEN:
        return

    data = obj.get("data", {})
    text = data.get("text") or ""
    if not text:
        return

    confidence = float(data.get("confidence") or 0.75)
    company_id = data.get("company_id")
    source_doc_id = data.get("source_document_id")
    status = data.get("status") or "unverified"

    source_ids = []
    if source_doc_id and source_doc_id in _BRIDGE_SEEN:
        source_ids = [_BRIDGE_SEEN[source_doc_id]]

    try:
        obs = graph.add_object("observation", {
            "text": text,
            "confidence": confidence,
            "low_confidence": confidence < 0.5,
            "source_ids": source_ids,
            "category": "fact",
            "metadata": {
                "company_id": company_id,
                "diligence_claim_id": claim_id,
                "diligence_claim_status": status,
                "bridge": "diligence_core",
            },
        })
        _BRIDGE_SEEN[claim_id] = obs.id
        try:
            graph.add_relation(obs.id, claim_id, "derived_from")
        except Exception:
            pass
        for sid in source_ids:
            try:
                graph.add_relation(sid, obs.id, "grounds")
            except Exception:
                pass
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# memo  →  artifact
# ──────────────────────────────────────────────────────────────────────────────

@behavior(
    name="memo_to_artifact",
    on=["object.created"],
    where={"object.type": "memo"},
    creates=["artifact"],
)
def memo_to_artifact(event, graph, ctx, *, settings=None):
    """Map a Diligence memo to a Core artifact.

    Diligence memo fields: company_id, summary, thesis_questions_addressed,
                           key_claims, open_contradictions, risks
    Core artifact fields: kind, title, content, format, status, metadata
    """
    obj = event.payload.get("object", {})
    memo_id = obj.get("id")
    if not memo_id or memo_id in _BRIDGE_SEEN:
        return

    data = obj.get("data", {})
    company_id = data.get("company_id") or "unknown"
    summary = data.get("summary") or ""
    key_claims: list = data.get("key_claims") or []
    thesis_qs: list = data.get("thesis_questions_addressed") or []
    open_contras: Any = data.get("open_contradictions") or data.get("contradictions_note") or []
    risks: list = data.get("risks") or []

    content_parts = [f"# Diligence Memo: {company_id}\n"]
    if summary:
        content_parts.append(f"## Summary\n{summary}\n")
    if key_claims:
        content_parts.append("## Key Claims\n" + "\n".join(f"- {c}" for c in key_claims))
    if thesis_qs:
        content_parts.append("\n## Thesis Questions Addressed\n" + "\n".join(f"- {q}" for q in thesis_qs))
    if open_contras:
        if isinstance(open_contras, list):
            content_parts.append("\n## Open Contradictions\n" + "\n".join(f"- {c}" for c in open_contras))
        else:
            content_parts.append(f"\n## Open Contradictions\n{open_contras}")
    if risks:
        content_parts.append("\n## Risks\n" + "\n".join(f"- {r}" for r in risks))

    content = "\n".join(content_parts)

    try:
        art = graph.add_object("artifact", {
            "kind": "memo",
            "title": f"Diligence Memo: {company_id}",
            "content": content,
            "format": "markdown",
            "status": "draft",
            "metadata": {
                "company_id": company_id,
                "diligence_memo_id": memo_id,
                "bridge": "diligence_core",
            },
        })
        _BRIDGE_SEEN[memo_id] = art.id
        try:
            graph.add_relation(art.id, memo_id, "derived_from")
        except Exception:
            pass
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# risk  →  evaluation
# ──────────────────────────────────────────────────────────────────────────────

@behavior(
    name="risk_to_evaluation",
    on=["object.created"],
    where={"object.type": "risk"},
    creates=["evaluation"],
)
def risk_to_evaluation(event, graph, ctx, *, settings=None):
    """Map a Diligence risk to a Core evaluation.

    Diligence risk fields: title, description, severity, company_id, related_claim_ids
    Core evaluation fields: subject_id, subject_type, judgment, rationale, metadata
    """
    obj = event.payload.get("object", {})
    risk_id = obj.get("id")
    if not risk_id or risk_id in _BRIDGE_SEEN:
        return

    data = obj.get("data", {})
    title = data.get("title") or "Unspecified Risk"
    description = data.get("description") or ""
    severity = data.get("severity") or "medium"
    company_id = data.get("company_id")
    related_claim_ids: list = data.get("related_claim_ids") or []

    rationale = f"{title}: {description}" if description else title

    try:
        evl = graph.add_object("evaluation", {
            "subject_id": risk_id,
            "subject_type": "risk",
            "judgment": severity,
            "rationale": rationale,
            "evaluator": "diligence_core_bridge",
            "metadata": {
                "company_id": company_id,
                "diligence_risk_id": risk_id,
                "related_claim_ids": related_claim_ids,
                "bridge": "diligence_core",
            },
        })
        _BRIDGE_SEEN[risk_id] = evl.id
        try:
            graph.add_relation(evl.id, risk_id, "derived_from")
        except Exception:
            pass
    except Exception:
        pass


BEHAVIORS = [
    document_to_source,
    claim_to_observation,
    memo_to_artifact,
    risk_to_evaluation,
]
