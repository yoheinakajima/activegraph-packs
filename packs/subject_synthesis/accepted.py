"""Accepted understanding work that is not yet resolved (Phase 5c closure).

The comprehension side of the accepted-work projection: synthesis
requests, staged comprehension reductions, and campaigns that the owner's
decisions authorized but that have not reached a terminal, owner-visible
outcome. Hosts render these rows next to the connector rows from
``packs.connector_control.accepted`` — one vocabulary, no inference from
incidental objects.
"""

from __future__ import annotations

from typing import Any


def accepted_understanding_work_fn(reader) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for obj in reader.objects(type="subject_synthesis_request"):
        data = obj.data or {}
        status = str(data.get("status") or "")
        if status == "proposed":
            rows.append({
                "kind": "synthesis_request",
                "ref": obj.id,
                "state": "queued",
                "reason": None,
                "label": str(data.get("reason") or "understanding synthesis"),
            })
        elif status == "failed":
            rows.append({
                "kind": "synthesis_request",
                "ref": obj.id,
                "state": "failed",
                "reason": str(data.get("error") or "synthesis failed"),
                "label": str(data.get("reason") or "understanding synthesis"),
            })
    for obj in reader.objects(type="comprehension_request"):
        data = obj.data or {}
        status = str(data.get("status") or "")
        state = {
            "proposed": "queued",
            "reducing": "executing",
            "aggregating": "executing",
            "failed": "failed",
        }.get(status)
        if state is None:
            continue
        counts = dict(data.get("counts") or {})
        rows.append({
            "kind": "comprehension_request",
            "ref": obj.id,
            "state": state,
            "reason": (
                str(data.get("error") or "comprehension failed")
                if state == "failed" else None
            ),
            "label": f"{data.get('service') or 'source'} comprehension",
            "progress": {
                "batches_done": int(counts.get("batches_completed") or 0),
                "batches": int(counts.get("batches") or 0),
                "items": int(counts.get("items") or 0),
            },
            "source_surface_id": str(data.get("source_surface_id") or ""),
        })
    for obj in reader.objects(type="comprehension_campaign"):
        data = obj.data or {}
        status = str(data.get("status") or "")
        if status == "open":
            rows.append({
                "kind": "campaign",
                "ref": obj.id,
                "state": "executing",
                "reason": None,
                "label": "understanding coordination",
                "selected_affordances": list(
                    data.get("selected_affordances") or []
                ),
            })
        elif status == "paused_owner":
            rows.append({
                "kind": "campaign",
                "ref": obj.id,
                "state": "blocked",
                "reason": "waiting for your answer",
                "label": "understanding coordination",
                "selected_affordances": list(
                    data.get("selected_affordances") or []
                ),
            })
    rows.sort(key=lambda row: (row["kind"], row["ref"]))
    return rows


__all__ = ["accepted_understanding_work_fn"]
