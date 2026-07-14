"""Durable external-work attempts (ADR 0041 as amended by ADR 0045).

The three-phase seam split slow work off the engine thread; this module makes
it durable. Every externally-performing work unit (a research plan execution,
an extraction request, a synthesis request, a comprehension batch) records an
attempt ledger in the graph keyed by an idempotency key, with enough persisted
state to distinguish prepared, performing, commit-pending, and terminal
attempts. The invariant the ledger buys: a crash after perform and before
commit can neither duplicate the external call nor lose its result — restart
finds the persisted outcome and commits it, retries under the explicit
attempt policy, or surfaces blocked/failed state. Process-local attempted
sets are not a lifecycle; this is.

The host pump drives the protocol:

    step = begin_external_attempt_fn(graph, kind=…, idempotency_key=…, …)
    if step["action"] == "perform":
        mark_attempt_performing_fn(graph, step["attempt_id"])
        outcome = <network work, off the engine thread>
        store_attempt_outcome_fn(graph, step["attempt_id"], outcome)
        <commit through the owning seam>
        mark_attempt_committed_fn(graph, step["attempt_id"])
    elif step["action"] == "commit":
        <commit step["outcome"] through the owning seam; never re-perform>
        mark_attempt_committed_fn(graph, step["attempt_id"])

Nothing here knows a provider or a service field.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional


ATTEMPT_INLINE_LIMIT_BYTES = 256 * 1024
DEFAULT_MAX_ATTEMPTS = 2

ATTEMPT_OPEN_PHASES = frozenset({"prepared", "performing", "commit_pending"})
ATTEMPT_TERMINAL_PHASES = frozenset({"committed", "failed", "blocked"})


def _stable_attempt_id(idempotency_key: str, attempt_number: int) -> str:
    material = f"{idempotency_key}\x1f{attempt_number}".encode("utf-8")
    return f"external_attempt_{hashlib.sha256(material).hexdigest()}"


def _encode_bounded(value: Any, *, field: str) -> tuple[Optional[str], Optional[str]]:
    """JSON-encode, secret-scan, and bound a payload/outcome for the ledger.

    Returns (encoded, refusal_reason). Oversized material is refused loudly —
    a truncated outcome could commit a lie, so the attempt blocks instead.
    """
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError) as exc:
        return None, f"{field}_not_serializable: {exc}"
    if len(encoded.encode("utf-8")) > ATTEMPT_INLINE_LIMIT_BYTES:
        return None, f"{field}_exceeds_inline_limit"
    from packs.tool_gateway.sanitizer import sanitize_output

    sanitized, _ = sanitize_output(encoded)
    return sanitized, None


def _attempts_for_key(reader, idempotency_key: str) -> list[Any]:
    rows = [
        obj for obj in reader.objects(type="external_work_attempt")
        if obj.data.get("idempotency_key") == idempotency_key
    ]
    rows.sort(key=lambda obj: int(obj.data.get("attempt_number") or 0))
    return rows


def begin_external_attempt_fn(
    graph,
    *,
    kind: str,
    idempotency_key: str,
    work_ref: str,
    payload: Any,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    reader=None,
) -> dict[str, Any]:
    """Open (or resume) the attempt ledger for one work unit.

    Actions returned:
      perform    — a fresh attempt row exists in ``prepared``; perform it.
      commit     — a prior attempt persisted its outcome before a crash;
                   commit the returned outcome, never re-perform.
      blocked    — the latest attempt is blocked; surface it, do nothing.
      exhausted  — the explicit attempt policy is spent; surface failure.
    """
    view = reader or graph
    if not kind.strip():
        raise ValueError("kind is required")
    if not idempotency_key.strip():
        raise ValueError("idempotency_key is required")
    rows = _attempts_for_key(view, idempotency_key)
    latest = rows[-1] if rows else None
    if latest is not None:
        phase = latest.data.get("phase")
        if phase == "commit_pending":
            outcome_json = latest.data.get("outcome_json") or "null"
            payload_json = latest.data.get("payload_json") or "null"
            return {
                "action": "commit",
                "attempt_id": latest.id,
                "attempt_number": int(latest.data.get("attempt_number") or 1),
                "payload": json.loads(payload_json),
                "outcome": json.loads(outcome_json),
            }
        if phase == "blocked":
            return {
                "action": "blocked",
                "attempt_id": latest.id,
                "reason": latest.data.get("blocked_reason") or "blocked",
            }
        if phase in ("prepared", "performing"):
            # A prior process died in flight. Whether the external call ran is
            # unknowable; the row is superseded by an explicit retry so the
            # crash stays visible instead of vanishing into a fresh row.
            graph.patch_object(latest.id, {
                "phase": "failed",
                "error": "superseded_by_retry_after_crash",
            })
    prior_count = len(_attempts_for_key(view, idempotency_key))
    next_number = prior_count + 1
    if next_number > max(1, int(max_attempts)):
        return {
            "action": "exhausted",
            "attempts": prior_count,
            "max_attempts": int(max_attempts),
        }
    payload_json, refusal = _encode_bounded(payload, field="payload")
    if refusal is not None:
        blocked = graph.add_object("external_work_attempt", {
            "attempt_identity": _stable_attempt_id(idempotency_key, next_number),
            "idempotency_key": idempotency_key,
            "kind": kind,
            "work_ref": work_ref,
            "phase": "blocked",
            "attempt_number": next_number,
            "max_attempts": int(max_attempts),
            "blocked_reason": refusal,
        })
        return {"action": "blocked", "attempt_id": blocked.id, "reason": refusal}
    attempt = graph.add_object("external_work_attempt", {
        "attempt_identity": _stable_attempt_id(idempotency_key, next_number),
        "idempotency_key": idempotency_key,
        "kind": kind,
        "work_ref": work_ref,
        "phase": "prepared",
        "attempt_number": next_number,
        "max_attempts": int(max_attempts),
        "payload_json": payload_json,
    })
    return {
        "action": "perform",
        "attempt_id": attempt.id,
        "attempt_number": next_number,
        "payload": payload,
    }


def mark_attempt_performing_fn(graph, attempt_id: str) -> None:
    graph.patch_object(attempt_id, {"phase": "performing"})


def store_attempt_outcome_fn(graph, attempt_id: str, outcome: Any) -> dict[str, Any]:
    """Persist the perform outcome before commit. After this write, restart
    commits from the ledger and never re-issues the external call."""
    outcome_json, refusal = _encode_bounded(outcome, field="outcome")
    if refusal is not None:
        graph.patch_object(attempt_id, {
            "phase": "blocked", "blocked_reason": refusal,
        })
        return {"ok": False, "reason": refusal}
    graph.patch_object(attempt_id, {
        "phase": "commit_pending", "outcome_json": outcome_json,
    })
    return {"ok": True}


def mark_attempt_committed_fn(graph, attempt_id: str, *, note: str = "") -> None:
    patch: dict[str, Any] = {"phase": "committed"}
    if note:
        patch["note"] = note
    graph.patch_object(attempt_id, patch)


def mark_attempt_failed_fn(
    graph, attempt_id: str, error: str, *, blocked: bool = False
) -> None:
    if blocked:
        graph.patch_object(attempt_id, {
            "phase": "blocked", "blocked_reason": error[:2000],
        })
    else:
        graph.patch_object(attempt_id, {
            "phase": "failed", "error": error[:2000],
        })


def pending_commit_attempts_fn(reader) -> list[dict[str, Any]]:
    """Attempts whose outcome is persisted but uncommitted — restart work."""
    rows: list[dict[str, Any]] = []
    for obj in reader.objects(type="external_work_attempt"):
        if obj.data.get("phase") != "commit_pending":
            continue
        rows.append({
            "attempt_id": obj.id,
            "kind": str(obj.data.get("kind") or ""),
            "idempotency_key": str(obj.data.get("idempotency_key") or ""),
            "work_ref": str(obj.data.get("work_ref") or ""),
        })
    rows.sort(key=lambda row: (row["kind"], row["idempotency_key"]))
    return rows


def attempt_ledger_for_key_fn(reader, idempotency_key: str) -> list[dict[str, Any]]:
    return [
        {
            "attempt_id": obj.id,
            "attempt_number": int(obj.data.get("attempt_number") or 0),
            "phase": str(obj.data.get("phase") or ""),
            "error": obj.data.get("error"),
            "blocked_reason": obj.data.get("blocked_reason"),
        }
        for obj in _attempts_for_key(reader, idempotency_key)
    ]


def project_external_work_attempts_fn(reader, *, limit: int = 100) -> dict[str, Any]:
    """Operational projection: every failure and retry, never a silent one."""
    by_key: dict[str, list[Any]] = {}
    for obj in reader.objects(type="external_work_attempt"):
        key = str(obj.data.get("idempotency_key") or "")
        by_key.setdefault(key, []).append(obj)
    rows: list[dict[str, Any]] = []
    failures = 0
    open_count = 0
    for key, objs in by_key.items():
        objs.sort(key=lambda o: int(o.data.get("attempt_number") or 0))
        latest = objs[-1]
        phase = str(latest.data.get("phase") or "")
        attempts = len(objs)
        had_failure = any(
            o.data.get("phase") in ("failed", "blocked") or o.data.get("error")
            for o in objs
        )
        if phase in ("failed", "blocked"):
            failures += 1
        if phase in ATTEMPT_OPEN_PHASES:
            open_count += 1
        rows.append({
            "idempotency_key": key,
            "kind": str(latest.data.get("kind") or ""),
            "work_ref": str(latest.data.get("work_ref") or ""),
            "phase": phase,
            "attempts": attempts,
            "retried": attempts > 1,
            "had_failure": had_failure,
            "error": latest.data.get("error"),
            "blocked_reason": latest.data.get("blocked_reason"),
        })
    rows.sort(key=lambda row: (row["phase"] not in ("failed", "blocked"), row["kind"], row["idempotency_key"]))
    return {
        "attempted_keys": len(rows),
        "open": open_count,
        "failed_or_blocked": failures,
        "attempts": rows[: max(1, int(limit))],
    }


__all__ = [
    "ATTEMPT_INLINE_LIMIT_BYTES",
    "DEFAULT_MAX_ATTEMPTS",
    "ATTEMPT_OPEN_PHASES",
    "ATTEMPT_TERMINAL_PHASES",
    "begin_external_attempt_fn",
    "mark_attempt_performing_fn",
    "store_attempt_outcome_fn",
    "mark_attempt_committed_fn",
    "mark_attempt_failed_fn",
    "pending_commit_attempts_fn",
    "attempt_ledger_for_key_fn",
    "project_external_work_attempts_fn",
]
