"""Gmail exploration, bounded sync, and effect proposals."""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from activegraph.packs import tool

from packs.tool_gateway.integrations import record_exploration_fn, stable_integration_id
from packs.usage.tools import CONNECTION_PATHS
from .family import materialize_gmail_run_fn


def _stable(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()}"


def _validated_route(route: str) -> str:
    normalized = str(route or "").strip().lower()
    if normalized not in CONNECTION_PATHS:
        raise ValueError(f"unknown Gmail connection route {route!r}")
    return normalized


def _capability_call(
    graph,
    *,
    operation: str,
    action_class: str,
    risk_class: str,
    input_data: dict[str, Any],
    metadata: dict[str, Any],
    proposed_by: str,
    route: str = "composio",
):
    route = _validated_route(route)
    explicit = action_class in {"R2", "R3", "R4"}
    return graph.add_object(
        "capability_call",
        {
            "provider_id": "",
            "provider_name": "gmail",
            "capability_name": operation,
            "input_data": input_data,
            "credential_ref_name": None,
            "credential_ref_id": None,
            "risk_class": risk_class,
            "action_class": action_class,
            "status": "proposed",
            "proposed_by": proposed_by,
            "frame_id": None,
            "proposed_at": None,
            "metadata": {
                "route": route,
                "gmail": metadata,
                "requires_explicit_approval": explicit,
            },
        },
    )


def request_gmail_exploration_fn(
    graph,
    *,
    user_id: str,
    connected_account_id: str,
    budget: int = 3,
    force: bool = False,
    route: str = "composio",
    sample_messages: int = 25,
) -> dict[str, Any]:
    route = _validated_route(route)
    if budget < 2:
        raise ValueError("Gmail exploration needs budget >= 2 for profile and labels")
    base_key = stable_integration_id("gmail_exploration", route, connected_account_id)
    prior = [
        obj for obj in graph.objects(type="integration_exploration")
        if str((((obj.data or {}).get("metadata") or {}).get("base_exploration_key") or "")) == base_key
        or str((((obj.data or {}).get("metadata") or {}).get("exploration_key") or "")) == base_key
    ]
    exploration_key = (
        stable_integration_id(
            "gmail_exploration_refresh", route, connected_account_id, len(prior) + 1
        )
        if force else base_key
    )
    existing = next(
        (
            obj for obj in graph.objects(type="integration_exploration")
            if ((obj.data or {}).get("metadata") or {}).get("exploration_key") == exploration_key
            and (obj.data or {}).get("status") in {"proposed", "partial", "completed"}
        ),
        None,
    )
    if existing:
        return {"ok": True, "created": False, "exploration_id": existing.id, "call_ids": existing.data.get("probe_call_ids") or []}

    probes: list[tuple[str, str, dict[str, Any]]] = [
        ("profile.get", "profile", {}),
        ("labels.list", "labels", {}),
    ]
    # Third bounded probe: newest message ids + dates only (no payloads), so
    # the ingestion plan can derive a measured recent-activity rate. Skipped
    # when the budget only covers the two structural probes.
    if budget >= 3 and sample_messages > 0:
        probes.append((
            "messages.fetch",
            "recent_activity",
            {"max_results": min(int(sample_messages), 100), "include_payload": False},
        ))
    calls = []
    for operation, probe, extra_input in probes:
        call = _capability_call(
            graph,
            operation=operation,
            action_class="R0",
            risk_class="low",
            input_data={
                "user_id": user_id,
                "connected_account_id": connected_account_id,
                **extra_input,
            },
            metadata={
                "kind": "exploration",
                "probe": probe,
                "exploration_key": exploration_key,
                "connected_account_id": connected_account_id,
            },
            proposed_by="gmail.integration_explorer",
            route=route,
        )
        calls.append(call)
    receipt, _ = record_exploration_fn(
        graph,
        service="gmail",
        account_ref=f"pending:{connected_account_id}",
        route=route,
        probe_call_ids=[call.id for call in calls],
        budget=budget,
        status="proposed",
        metadata={
            "exploration_key": exploration_key,
            "base_exploration_key": base_key,
            "connected_account_id": connected_account_id,
            "forced_refresh": force,
            "route": route,
            "probes": [probe for _operation, probe, _extra in probes],
            "user_id": user_id,
        },
    )
    # The generic helper's canonical identity also includes call ids; the stable
    # exploration key is stored for result correlation and replay.
    return {"ok": True, "created": True, "exploration_id": receipt.id, "call_ids": [call.id for call in calls]}


def request_gmail_backfill_fn(
    graph,
    *,
    source_surface_id: str,
    account_ref: str,
    user_id: str,
    connected_account_id: str,
    route: str = "composio",
    plan_ref: Optional[str] = None,
) -> dict[str, Any]:
    """Start the bounded backfill the approved ingestion plan describes.

    Every bounded acquisition binds to the exact approved plan version
    (ADR 0039): window, caps, and page size come from the plan, and a run
    without a current approved plan fails loud.
    """
    from packs.connector_control.plans import (
        bind_plan_execution_fn,
        current_plan_for_surface_fn,
        resolve_plan_fn,
    )
    from .plan import plan_backfill_query

    route = _validated_route(route)
    plan = (
        resolve_plan_fn(graph, plan_ref)
        if plan_ref
        else current_plan_for_surface_fn(graph, source_surface_id)
    )
    if plan is None:
        raise ValueError(
            f"no ingestion plan exists for surface {source_surface_id!r}; a "
            "Gmail backfill only runs against a current approved plan — "
            "propose and approve one first (ADR 0039)"
        )
    plan_data = dict(plan.data or {})
    if plan_data.get("source_surface_id") != source_surface_id:
        raise ValueError(
            f"plan {plan_data.get('plan_identity')!r} belongs to surface "
            f"{plan_data.get('source_surface_id')!r}, not {source_surface_id!r}"
        )
    plan_caps = dict(plan_data.get("caps") or {})
    query = plan_backfill_query(plan_data)
    page_size = int(plan_caps.get("page_size") or 25)
    max_messages = int(plan_caps.get("max_items") or 0)
    max_pages = int(plan_caps.get("max_pages") or 0)
    if max_messages < 1 or max_pages < 1:
        raise ValueError(
            f"plan {plan_data.get('plan_identity')!r} carries no usable caps"
        )
    plan_status = plan_data.get("status")
    identity = _stable("gmail_sync", source_surface_id, "backfill", query, max_messages, max_pages)
    existing = next((obj for obj in graph.objects(type="gmail_sync_run") if (obj.data or {}).get("run_identity") == identity), None)
    if existing and (existing.data or {}).get("status") in {"running", "completed", "partial"}:
        return {"ok": True, "created": False, "run_id": existing.id, "call_ids": existing.data.get("call_ids") or []}
    if plan_status not in {"approved", "executing"}:
        raise ValueError(
            f"ingestion plan {plan_data.get('plan_identity')!r} is "
            f"{plan_status!r}; only a current approved plan can execute "
            "(a superseded plan can never execute)"
        )
    if existing and (existing.data or {}).get("status") == "failed":
        active_profile = next(
            (
                obj for obj in graph.objects(type="integration_profile")
                if obj.data.get("service") == "gmail"
                and obj.data.get("account_ref") == account_ref
                and obj.data.get("status") == "active"
            ),
            None,
        )
        prior_profile_id = ((existing.data or {}).get("metadata") or {}).get("integration_profile_id")
        structural_failure = (existing.data or {}).get("error_code") in {"unexpected_shape", "auth_expired"}
        if structural_failure and (active_profile is None or active_profile.id == prior_profile_id):
            return {
                "ok": False,
                "created": False,
                "retry_blocked": True,
                "run_id": existing.id,
                "call_ids": existing.data.get("call_ids") or [],
                "reason": "reconnect or complete profile re-exploration before retrying this backfill",
            }
        metadata = dict((existing.data or {}).get("metadata") or {})
        metadata["route"] = route
        if active_profile is not None:
            metadata["integration_profile_id"] = active_profile.id
        graph.patch_object(
            existing.id,
            {
                "status": "running",
                "error_code": None,
                "error": None,
                "next_page_token": None,
                "metadata": metadata,
            },
            rationale="resume Gmail backfill from query overlap after interruption",
        )
        # A failed run released its plan back to approved; re-bind the resume.
        bind_plan_execution_fn(
            graph,
            plan_ref=str(plan_data.get("plan_identity") or ""),
            domain_run_id=existing.id,
            source_surface_id=source_surface_id,
        )
        call = propose_gmail_page_fn(graph, graph.get_object(existing.id))
        return {"ok": True, "created": False, "resumed": True, "run_id": existing.id, "call_ids": [call.id]}
    active_profile = next(
        (
            obj for obj in graph.objects(type="integration_profile")
            if obj.data.get("service") == "gmail"
            and obj.data.get("account_ref") == account_ref
            and obj.data.get("status") == "active"
        ),
        None,
    )
    run = graph.add_object(
        "gmail_sync_run",
        {
            "run_identity": identity,
            "source_surface_id": source_surface_id,
            "account_ref": account_ref,
            "user_id": user_id,
            "connected_account_id": connected_account_id,
            "mode": "backfill",
            "query": query,
            "start_history_id": None,
            "next_page_token": None,
            "page_size": page_size,
            "max_messages": max_messages,
            "max_pages": max_pages,
            "pages_completed": 0,
            "messages_imported": 0,
            "call_ids": [],
            "pending_message_ids": [],
            "completed_message_ids": [],
            "status": "running",
            "latest_history_id": None,
            "deleted_message_ids": [],
            "tombstones_recorded": 0,
            "error_code": None,
            "error": None,
            "metadata": {
                "restart_strategy": "query_overlap_plus_evidence_dedup",
                "integration_profile_id": active_profile.id if active_profile else None,
                "route": route,
                "plan_identity": plan_data.get("plan_identity"),
                "plan_version": int(plan_data.get("version") or 0),
                "plan_object_id": plan.id,
            },
        },
    )
    bind_plan_execution_fn(
        graph,
        plan_ref=str(plan_data.get("plan_identity") or ""),
        domain_run_id=run.id,
        source_surface_id=source_surface_id,
    )
    call = propose_gmail_page_fn(graph, run)
    return {"ok": True, "created": True, "run_id": run.id, "call_ids": [call.id]}


def propose_gmail_page_fn(graph, run, *, page_token: str = ""):
    data = run.data or {}
    call = _capability_call(
        graph,
        operation="messages.fetch",
        action_class="R0",
        risk_class="low",
        input_data={
            "user_id": data["user_id"],
            "connected_account_id": data["connected_account_id"],
            "query": data.get("query") or "",
            "page_token": page_token,
            "max_results": int(data["page_size"]),
            "include_payload": True,
        },
        metadata={"kind": "backfill", "run_id": run.id, "page_sequence": int(data.get("pages_completed", 0)) + 1},
        proposed_by="gmail.backfill",
        route=str((data.get("metadata") or {}).get("route") or "composio"),
    )
    graph.add_relation(run.id, call.id, "gmail_sync_call")
    # This helper is also called from a behavior, where ``graph`` is the
    # provenance-bound BehaviorGraph facade. Its patch API deliberately owns
    # actor/rationale metadata and accepts only target + updates.
    graph.patch_object(
        run.id,
        {
            "call_ids": [*(data.get("call_ids") or []), call.id],
            "next_page_token": page_token or None,
        },
    )
    return call


def request_gmail_poll_fn(
    graph,
    *,
    source_surface_id: str,
    account_ref: str,
    user_id: str,
    connected_account_id: str,
    start_history_id: str,
    max_messages: int = 100,
    route: str = "composio",
) -> dict[str, Any]:
    route = _validated_route(route)
    if not start_history_id:
        raise ValueError("start_history_id is required")
    matching = [
        obj for obj in graph.objects(type="gmail_sync_run")
        if obj.data.get("source_surface_id") == source_surface_id
        and obj.data.get("mode") == "poll"
        and obj.data.get("start_history_id") == start_history_id
    ]
    active = next((obj for obj in reversed(matching) if obj.data.get("status") in {"proposed", "running"}), None)
    if active is not None:
        return {"ok": True, "created": False, "already_running": True, "run_id": active.id, "call_ids": active.data.get("call_ids") or []}
    existing = matching[-1] if matching else None
    if existing:
        if (existing.data or {}).get("status") == "failed" and (existing.data or {}).get("error_code") == "rate_limited":
            graph.patch_object(
                existing.id,
                {"status": "running", "error_code": None, "error": None},
                rationale="retry Gmail history poll after a recorded rate limit",
            )
            retry = _capability_call(
                graph,
                operation="history.list", action_class="R0", risk_class="low",
                input_data={
                    "user_id": user_id,
                    "connected_account_id": connected_account_id,
                    "start_history_id": start_history_id,
                    "max_results": max_messages,
                },
                metadata={"kind": "history", "run_id": existing.id, "retry": True},
                proposed_by="gmail.poll",
                route=route,
            )
            graph.add_relation(existing.id, retry.id, "gmail_sync_call")
            graph.patch_object(
                existing.id,
                {"call_ids": [*(existing.data.get("call_ids") or []), retry.id]},
                rationale="Gmail history retry proposed",
            )
            return {"ok": True, "created": False, "resumed": True, "run_id": existing.id, "call_ids": [retry.id]}
    identity = _stable("gmail_sync", source_surface_id, "poll", start_history_id, len(matching) + 1)
    run = graph.add_object(
        "gmail_sync_run",
        {
            "run_identity": identity,
            "source_surface_id": source_surface_id,
            "account_ref": account_ref,
            "user_id": user_id,
            "connected_account_id": connected_account_id,
            "mode": "poll",
            "query": "",
            "start_history_id": start_history_id,
            "next_page_token": None,
            "page_size": min(max_messages, 100),
            "max_messages": max_messages,
            "max_pages": 1,
            "pages_completed": 0,
            "messages_imported": 0,
            "call_ids": [],
            "pending_message_ids": [],
            "completed_message_ids": [],
            "status": "running",
            "latest_history_id": None,
            "deleted_message_ids": [],
            "tombstones_recorded": 0,
            "error_code": None,
            "error": None,
            "metadata": {"route": route},
        },
    )
    call = _capability_call(
        graph,
        operation="history.list", action_class="R0", risk_class="low",
        input_data={
            "user_id": user_id, "connected_account_id": connected_account_id,
            "start_history_id": start_history_id, "max_results": max_messages,
        },
        metadata={"kind": "history", "run_id": run.id},
        proposed_by="gmail.poll",
        route=route,
    )
    graph.add_relation(run.id, call.id, "gmail_sync_call")
    graph.patch_object(run.id, {"call_ids": [call.id]}, rationale="Gmail history poll proposed")
    return {"ok": True, "created": True, "run_id": run.id, "call_ids": [call.id]}


def create_gmail_draft_candidate_fn(
    graph,
    *,
    account_ref: str,
    connected_account_id: str,
    to: list[str],
    subject: str,
    body: str,
    cc: Optional[list[str]] = None,
    bcc: Optional[list[str]] = None,
    thread_id: Optional[str] = None,
    prediction_id: Optional[str] = None,
    route: str = "composio",
):
    route = _validated_route(route)
    identity = _stable("gmail_draft", account_ref, ",".join(to), subject, body, thread_id or "")
    existing = next((obj for obj in graph.objects(type="gmail_draft_candidate") if (obj.data or {}).get("draft_identity") == identity), None)
    if existing:
        return existing, False
    return graph.add_object(
        "gmail_draft_candidate",
        {
            "draft_identity": identity,
            "account_ref": account_ref,
            "connected_account_id": connected_account_id,
            "to": to, "cc": cc or [], "bcc": bcc or [],
            "subject": subject, "body": body, "thread_id": thread_id,
            "status": "local_draft", "provider_draft_id": None,
            "prediction_id": prediction_id,
            "idempotency_key": identity,
            "metadata": {"route": route},
        },
    ), True


def reprocess_gmail_evidence_fn(
    graph, *, source_surface_id: str, max_items: int = 250
) -> dict[str, Any]:
    """Re-run local family projection over recorded evidence; never contact Gmail."""
    if max_items < 1 or max_items > 1_000:
        raise ValueError("max_items must be between 1 and 1000")
    run_ids = {
        str((obj.data.get("normalized_metadata") or {}).get("connector_run_id") or "")
        for obj in graph.objects(type="activity_evidence")
        if obj.data.get("status") == "current"
        and obj.data.get("source_surface_id") == source_surface_id
    }
    runs = [
        obj for obj in graph.objects(type="gmail_sync_run")
        if obj.id in run_ids
    ]
    remaining = max_items
    summaries = []
    for run in runs:
        if remaining <= 0:
            break
        _native, summary = materialize_gmail_run_fn(
            graph, run, reader=graph, reprocess=True, max_items=remaining
        )
        summaries.append({"run_id": run.id, **summary})
        remaining -= int(summary.get("messages") or 0)
    return {
        "ok": True,
        "source_surface_id": source_surface_id,
        "messages_reprocessed": max_items - remaining,
        "runs": summaries,
        "provider_contacted": False,
    }


def _existing_draft_call(graph, draft_id: str, kind: str):
    return next(
        (
            obj
            for obj in graph.objects(type="capability_call")
            if (((obj.data or {}).get("metadata") or {}).get("gmail") or {}).get("kind") == kind
            and (((obj.data or {}).get("metadata") or {}).get("gmail") or {}).get("draft_id") == draft_id
            and (obj.data or {}).get("status") not in {"failed", "rejected"}
        ),
        None,
    )


def request_gmail_draft_sync_fn(graph, *, draft_id: str, user_id: str) -> dict[str, Any]:
    """Propose the R2 transition from a local draft to a Gmail-hosted draft."""

    draft = graph.get_object(draft_id)
    if draft is None or draft.type != "gmail_draft_candidate":
        raise ValueError("gmail draft candidate not found")
    if draft.data.get("status") in {"synced", "send_proposed", "sent"}:
        return {
            "ok": True,
            "created": False,
            "draft_id": draft.id,
            "provider_draft_id": draft.data.get("provider_draft_id"),
        }
    existing = _existing_draft_call(graph, draft.id, "draft_create")
    if existing:
        return {"ok": True, "created": False, "draft_id": draft.id, "call_id": existing.id}
    recipients = [str(value).strip() for value in draft.data.get("to") or [] if str(value).strip()]
    if not recipients:
        raise ValueError("Gmail draft needs at least one recipient")
    call = _capability_call(
        graph,
        operation="drafts.create",
        action_class="R2",
        risk_class="medium",
        input_data={
            "user_id": user_id,
            "connected_account_id": draft.data["connected_account_id"],
            "recipient_email": ", ".join(recipients),
            "subject": draft.data.get("subject") or "",
            "body": draft.data.get("body") or "",
            "cc": list(draft.data.get("cc") or []),
            "bcc": list(draft.data.get("bcc") or []),
            "thread_id": draft.data.get("thread_id") or "",
            "is_html": False,
            "idempotency_key": draft.data["idempotency_key"],
        },
        metadata={"kind": "draft_create", "draft_id": draft.id},
        proposed_by="gmail.local_draft",
        route=str((draft.data.get("metadata") or {}).get("route") or "composio"),
    )
    graph.add_relation(draft.id, call.id, "gmail_draft_call")
    graph.patch_object(
        draft.id,
        {"status": "sync_proposed"},
        rationale="owner-visible Gmail draft synchronization proposed",
    )
    return {"ok": True, "created": True, "draft_id": draft.id, "call_id": call.id}


def request_gmail_draft_send_fn(graph, *, draft_id: str, user_id: str) -> dict[str, Any]:
    """Propose an outward R3 send; the gateway must still obtain approval."""

    draft = graph.get_object(draft_id)
    if draft is None or draft.type != "gmail_draft_candidate":
        raise ValueError("gmail draft candidate not found")
    if draft.data.get("status") == "sent":
        return {"ok": True, "created": False, "draft_id": draft.id, "sent": True}
    existing = _existing_draft_call(graph, draft.id, "draft_send")
    if existing:
        return {"ok": True, "created": False, "draft_id": draft.id, "call_id": existing.id}
    provider_draft_id = str(draft.data.get("provider_draft_id") or "")
    if draft.data.get("status") != "synced" or not provider_draft_id:
        raise ValueError("Gmail draft must be synchronized before send can be proposed")
    call = _capability_call(
        graph,
        operation="drafts.send",
        action_class="R3",
        risk_class="high",
        input_data={
            "user_id": user_id,
            "connected_account_id": draft.data["connected_account_id"],
            "draft_id": provider_draft_id,
            "idempotency_key": f"{draft.data['idempotency_key']}:send",
        },
        metadata={"kind": "draft_send", "draft_id": draft.id},
        proposed_by="gmail.synced_draft",
        route=str((draft.data.get("metadata") or {}).get("route") or "composio"),
    )
    graph.add_relation(draft.id, call.id, "gmail_draft_call")
    graph.patch_object(
        draft.id,
        {"status": "send_proposed"},
        rationale="outward Gmail send proposed; explicit R3 approval still required",
    )
    return {"ok": True, "created": True, "draft_id": draft.id, "call_id": call.id}


@tool(name="request_gmail_exploration", description="Propose budgeted R0 Gmail structure and activity probes.")
def request_gmail_exploration(graph, user_id: str = "", connected_account_id: str = "", budget: int = 3, force: bool = False, route: str = "composio", sample_messages: int = 25):
    return request_gmail_exploration_fn(graph, user_id=user_id, connected_account_id=connected_account_id, budget=budget, force=force, route=route, sample_messages=sample_messages)


@tool(name="request_gmail_backfill", description="Start the bounded resumable Gmail backfill its approved ingestion plan describes.")
def request_gmail_backfill(graph, source_surface_id: str = "", account_ref: str = "", user_id: str = "", connected_account_id: str = "", route: str = "composio", plan_ref: str = ""):
    return request_gmail_backfill_fn(graph, source_surface_id=source_surface_id, account_ref=account_ref, user_id=user_id, connected_account_id=connected_account_id, route=route, plan_ref=plan_ref or None)


@tool(name="propose_gmail_ingestion_plan", description="Derive Gmail's ingestion-plan proposal from the recorded mailbox topology.")
def propose_gmail_ingestion_plan(graph, source_surface_id: str = "", account_ref: str = "", purpose: str = "initial_backfill", window_days: int = 0):
    from .plan import propose_gmail_ingestion_plan_fn

    return propose_gmail_ingestion_plan_fn(
        graph,
        source_surface_id=source_surface_id,
        account_ref=account_ref,
        purpose=purpose,
        window_days=window_days or None,
    )


@tool(name="request_gmail_poll", description="Poll Gmail from a provider-stable history watermark.")
def request_gmail_poll(graph, source_surface_id: str = "", account_ref: str = "", user_id: str = "", connected_account_id: str = "", start_history_id: str = "", max_messages: int = 100, route: str = "composio"):
    return request_gmail_poll_fn(graph, source_surface_id=source_surface_id, account_ref=account_ref, user_id=user_id, connected_account_id=connected_account_id, start_history_id=start_history_id, max_messages=max_messages, route=route)


@tool(name="create_gmail_draft_candidate", description="Create a local R1 Gmail draft candidate; no provider write or send.")
def create_gmail_draft_candidate(graph, account_ref: str = "", connected_account_id: str = "", to: Optional[list[str]] = None, subject: str = "", body: str = "", route: str = "composio"):
    draft, created = create_gmail_draft_candidate_fn(graph, account_ref=account_ref, connected_account_id=connected_account_id, to=to or [], subject=subject, body=body, route=route)
    return {"ok": True, "created": created, "draft_id": draft.id}


@tool(name="request_gmail_draft_sync", description="Propose an R2 Gmail-hosted draft from a local R1 candidate.")
def request_gmail_draft_sync(graph, draft_id: str = "", user_id: str = ""):
    return request_gmail_draft_sync_fn(graph, draft_id=draft_id, user_id=user_id)


@tool(name="request_gmail_draft_send", description="Propose an outward R3 send of an existing Gmail draft.")
def request_gmail_draft_send(graph, draft_id: str = "", user_id: str = ""):
    return request_gmail_draft_send_fn(graph, draft_id=draft_id, user_id=user_id)


@tool(name="reprocess_gmail_evidence", description="Re-run local Gmail family projection over recorded evidence without provider contact.")
def reprocess_gmail_evidence(graph, source_surface_id: str = "", max_items: int = 250):
    return reprocess_gmail_evidence_fn(
        graph, source_surface_id=source_surface_id, max_items=max_items
    )


TOOLS = [
    request_gmail_exploration,
    request_gmail_backfill,
    propose_gmail_ingestion_plan,
    request_gmail_poll,
    create_gmail_draft_candidate,
    request_gmail_draft_sync,
    request_gmail_draft_send,
    reprocess_gmail_evidence,
]

__all__ = [
    "request_gmail_exploration_fn", "request_gmail_backfill_fn", "request_gmail_poll_fn",
    "propose_gmail_page_fn", "create_gmail_draft_candidate_fn",
    "request_gmail_draft_sync_fn", "request_gmail_draft_send_fn",
    "reprocess_gmail_evidence_fn", "TOOLS",
]
