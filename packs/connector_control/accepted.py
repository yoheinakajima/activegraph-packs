"""Accepted external work that is not yet terminal (Phase 5c closure).

The 2026-07-16 owner run proved that owner-authorized work can evaporate:
an approved plan sat silently ``approved`` forever with no visible state
between "the owner said yes" and "something ran". This module is the
neutral answer — one reusable projection of accepted work that is still
queued, executing, blocked, or failed, joined with the durable attempt
ledger so a host renders truthful progress instead of inferring it from
incidental objects. Rows are host-vocabulary-free: kinds and states only.

States:
  queued     accepted (approved) and awaiting its first perform
  executing  bound to a live run / attempt in flight
  blocked    the ledger refuses to continue without intervention
             (owner-readable ``reason`` present)
  failed     the explicit attempt policy is exhausted or the work settled
             failed (owner-readable ``reason`` present)
"""

from __future__ import annotations

from typing import Any

from .attempts import DEFAULT_MAX_ATTEMPTS


def _latest_attempts_by_key(reader) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for obj in reader.objects(type="external_work_attempt"):
        key = str(obj.data.get("idempotency_key") or "")
        if not key:
            continue
        current = latest.get(key)
        if current is None or int(obj.data.get("attempt_number") or 0) > int(
            current.data.get("attempt_number") or 0
        ):
            latest[key] = obj
    return latest


def accepted_plan_work_fn(reader) -> list[dict[str, Any]]:
    """Approved/executing ingestion plans with ledger-derived liveness.

    An approved plan is accepted work the moment the approval commits —
    independent of coordinator windows, review state, hatch, or restart.
    Every row is one owner-approved contract that has not reached a
    terminal plan state; a host that renders these rows can never lose
    accepted work silently again.
    """
    attempts = _latest_attempts_by_key(reader)
    rows: list[dict[str, Any]] = []
    for plan in reader.objects(type="connector_ingestion_plan"):
        data = plan.data or {}
        status = str(data.get("status") or "")
        if status not in ("approved", "executing"):
            continue
        identity = str(data.get("plan_identity") or "")
        key = f"plan:{identity}:v{int(data.get('version') or 0)}"
        latest = attempts.get(key)
        state = "executing" if status == "executing" else "queued"
        reason = None
        if latest is not None:
            phase = str(latest.data.get("phase") or "")
            attempt_number = int(latest.data.get("attempt_number") or 1)
            max_attempts = int(
                latest.data.get("max_attempts") or DEFAULT_MAX_ATTEMPTS
            )
            policy_spent = attempt_number >= max_attempts
            released_run = (data.get("metadata") or {}).get(
                "released_after_failed_run"
            )
            if phase == "blocked":
                state = "blocked"
                reason = str(latest.data.get("blocked_reason") or "blocked")
            elif phase == "failed" and status == "approved":
                if policy_spent:
                    state = "failed"
                    reason = str(
                        latest.data.get("error") or "attempt policy exhausted"
                    )
                else:
                    state = "queued"  # released for an explicit bounded retry
                    reason = str(latest.data.get("error") or "") or None
            elif phase in ("prepared", "performing") and status == "approved":
                state = "executing"
            elif (
                phase == "committed" and status == "approved"
                and released_run and policy_spent
            ):
                # Every attempt committed a run and every run failed: the
                # ledger will refuse the next begin, so nothing can ever
                # execute this plan again. Silent "queued" here is the
                # 2026-07-16 zombie in a new costume — surface it failed.
                state = "failed"
                reason = (
                    "the acquisition failed and the retry budget is spent "
                    f"(last run {released_run})"
                )
        rows.append({
            "kind": "ingestion_plan",
            "ref": identity,
            "work_key": key,
            "service": str(data.get("service") or ""),
            "purpose": str(data.get("purpose") or "initial_backfill"),
            "source_surface_id": str(data.get("source_surface_id") or ""),
            "state": state,
            "reason": reason,
            "label": (
                f"{data.get('service')} {data.get('purpose') or 'acquisition'}"
            ),
        })
    rows.sort(key=lambda row: (row["service"], row["ref"]))
    return rows


def settle_exhausted_plans_fn(graph, reader=None) -> list[dict[str, Any]]:
    """Move approved plans whose attempt policy is spent to a terminal,
    owner-readable outcome (Gate B terminality).

    An approved plan whose latest ledger attempt is blocked, or failed with
    the explicit attempt policy exhausted, has no live execution vehicle
    left: leaving it ``approved`` would hold every dependent (lenses,
    campaigns, onboarding gates) open forever while nothing can ever run.
    Abandoning it with the ledger's reason keeps the failure visible and
    terminal — the safe retry path is an explicit re-proposal, which mints
    a fresh version with a fresh attempt budget. Idempotent and
    restart-safe; never converts unfinished work into success.
    """
    view = reader or graph
    from .plans import abandon_ingestion_plan_fn

    settled: list[dict[str, Any]] = []
    for row in accepted_plan_work_fn(view):
        if row["state"] not in ("failed", "blocked"):
            continue
        reason = str(row.get("reason") or "the attempt policy was exhausted")
        abandon_ingestion_plan_fn(
            graph,
            plan_ref=row["ref"],
            actor="system:attempt_policy",
            reason=reason,
        )
        settled.append({"ref": row["ref"], "reason": reason})
    return settled


__all__ = ["accepted_plan_work_fn", "settle_exhausted_plans_fn"]
