"""Gmail's ingestion-plan derivation and executor (ADR 0039 / D059).

The service owns the derivation rule — the first consumer of the recorded
``IntegrationProfile.data_topology`` — and the neutral plan contract owns
versioning, ceilings, verdicts, and run binding. Honest ignorance is a
first-class outcome: a mailbox the probes could not measure proposes the
service default and says so.
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional

from packs.communication.conversation import FAMILY_PROJECTOR_VERSION
from packs.communication.hygiene import FILTER_VERSION
from packs.connector_control.plans import (
    current_plan_for_surface_fn,
    propose_ingestion_plan_fn,
    register_deferred_plan_execution,
    register_ingestion_plan_executor,
)

from .family import MAPPER_VERSION
from .settings import GmailSettings


GMAIL_PLAN_PROPOSER = "gmail.plan_proposer@0.1.0"
GMAIL_INTERPRETATION_STAGES = [
    MAPPER_VERSION,
    FILTER_VERSION,
    FAMILY_PROJECTOR_VERSION,
    "semantic_extraction.selection@profile-routed",
]
# Gmail's default search scope already excludes these system containers; the
# plan says so instead of implying they will be read.
_DEFAULT_EXCLUDED_CONTAINERS = {"SPAM", "TRASH"}
_RENDERABLE_LABEL = re.compile(r"^[A-Za-z0-9_/\-]+$")


def _label_exclusion_supported(name: str) -> bool:
    return bool(_RENDERABLE_LABEL.match(name or ""))


def derive_gmail_window(
    data_topology: dict[str, Any],
    *,
    window_days: int,
    max_items: int,
    policy_id: str,
    policy_version: int,
    provenance: Optional[list[str]] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The Gmail derivation rule.

    - measured_topology: the activity sample measured a recent rate; the
      window estimate is ``min(ceil(rate * window_days), volume, cap)``.
    - volume_only: the profile probe measured totals but no dated sample ran.
    - service_default: an explored profile recorded no usable volume.
    - unknown_topology: no explored topology exists at all.
    """
    topology = dict(data_topology or {})
    volume = (topology.get("volume_estimate") or {}).get("messages")
    volume = int(volume) if isinstance(volume, (int, float)) and volume >= 0 else None
    sample = dict(topology.get("activity_sample") or {})
    rate = sample.get("messages_per_day")
    sampled = int(sample.get("sampled_messages") or 0)
    span_days = sample.get("sampled_span_days")
    refs = list(provenance or [])

    cap_note = f"cap {max_items} messages ({policy_id} v{policy_version})"
    if rate is not None and sampled >= 2 and span_days:
        estimate = min(
            int(math.ceil(float(rate) * window_days)),
            volume if volume is not None else int(math.ceil(float(rate) * window_days)),
            max_items,
        )
        window = {
            "kind": "recent_days", "days": window_days, "estimated_items": estimate,
        }
        derivation = {
            "basis": "measured_topology",
            "summary": (
                f"mailbox holds ~{volume if volume is not None else 'an unmeasured number of'} "
                f"messages; the newest {sampled} span {span_days} days "
                f"(≈{rate}/day), so the most recent {window_days} days ≈ "
                f"{estimate} messages; full history depth stays unmeasured — "
                f"{cap_note}"
            ),
            "measurements": {
                "messages_total": volume,
                "sampled_messages": sampled,
                "sampled_span_days": span_days,
                "messages_per_day": rate,
                "window_estimate_items": estimate,
            },
            "provenance": refs,
        }
        return window, derivation
    if volume is not None:
        window = {"kind": "recent_days", "days": window_days, "estimated_items": None}
        derivation = {
            "basis": "volume_only",
            "summary": (
                f"mailbox holds ~{volume} messages; recent activity rate and "
                f"span are unmeasured, so the most recent {window_days} days "
                f"are proposed blind — {cap_note}"
            ),
            "measurements": {"messages_total": volume},
            "provenance": refs,
        }
        return window, derivation
    if refs:
        window = {"kind": "recent_days", "days": window_days, "estimated_items": None}
        derivation = {
            "basis": "service_default",
            "summary": (
                f"the explored profile recorded no usable mailbox volume; "
                f"proposing the service default: most recent {window_days} "
                f"days — {cap_note}"
            ),
            "measurements": {},
            "provenance": refs,
        }
        return window, derivation
    window = {"kind": "recent_days", "days": window_days, "estimated_items": None}
    derivation = {
        "basis": "unknown_topology",
        "summary": (
            f"no topology probe has recorded this mailbox's shape; proposing "
            f"the service default: most recent {window_days} days — {cap_note}"
        ),
        "measurements": {},
        "provenance": [],
    }
    return window, derivation


def _plan_surfaces(
    data_topology: dict[str, Any], signal_map: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    expectations = {
        str(row.get("surface") or ""): {
            key: row.get(key)
            for key in ("estimated_richness", "confidence", "measurement", "provenance")
            if row.get(key) is not None
        }
        for row in signal_map or []
    }
    surfaces: list[dict[str, Any]] = [{
        "surface_ref": "inbox",
        "label": "inbox",
        "included": True,
        "expectation": expectations.get("inbox") or {"estimated_richness": "unmeasured"},
    }]
    for container in (data_topology or {}).get("containers") or []:
        name = str(container.get("name") or container.get("id") or "")
        if not name or name == "INBOX":
            continue
        excluded = name.upper() in _DEFAULT_EXCLUDED_CONTAINERS
        expectation = dict(
            expectations.get(f"label:{name}")
            or {"estimated_richness": "unmeasured"}
        )
        expectation["exclusion_supported"] = _label_exclusion_supported(name)
        if excluded:
            expectation["note"] = "excluded by the service's default search scope"
        surfaces.append({
            "surface_ref": f"label:{name}",
            "label": name,
            "included": not excluded,
            "expectation": expectation,
        })
    return surfaces


def plan_backfill_query(plan_data: dict[str, Any]) -> str:
    """Render the approved plan's window and exclusions as a Gmail query."""
    window = dict(plan_data.get("window") or {})
    if plan_data.get("purpose") == "comprehension":
        # Sent-mail comprehension (ADR 0045): canonical Sent semantics via
        # the service's search scope — the latest-N bound comes from the
        # plan caps, so no date term joins the query.
        return "in:sent"
    days = int(window.get("days") or 30)
    terms = [f"newer_than:{days}d"]
    for surface in plan_data.get("surfaces") or []:
        if surface.get("included"):
            continue
        label = str(surface.get("label") or "")
        if label.upper() in _DEFAULT_EXCLUDED_CONTAINERS:
            continue  # already outside the default scope
        if not _label_exclusion_supported(label):
            raise ValueError(
                f"label {label!r} cannot be excluded from the Gmail query; "
                "re-include it or rename the label before executing this plan"
            )
        terms.append(f"-label:{label}")
    return " ".join(terms)


def propose_gmail_ingestion_plan_fn(
    graph,
    *,
    source_surface_id: str,
    account_ref: str,
    profile: Optional[Any] = None,
    purpose: str = "initial_backfill",
    window_days: Optional[int] = None,
    settings: Optional[GmailSettings] = None,
    reader=None,
) -> dict[str, Any]:
    """Derive and record Gmail's proposal from the recorded topology."""
    view = reader or graph
    configured = settings or GmailSettings()
    if profile is None:
        profile = next(
            (
                obj for obj in view.objects(type="integration_profile")
                if obj.data.get("service") == "gmail"
                and obj.data.get("account_ref") == account_ref
                and obj.data.get("status") == "active"
            ),
            None,
        )
    topology = dict((profile.data.get("data_topology") if profile else {}) or {})
    signal_map = list((profile.data.get("signal_map") if profile else []) or [])
    provenance = list((profile.data.get("exploration_receipts") if profile else []) or [])
    if profile is not None:
        provenance.append(profile.id)

    policy = next(
        (
            obj for obj in view.objects(type="connector_operational_policy")
            if obj.data.get("status") == "active"
        ),
        None,
    )
    if policy is None:
        raise ValueError(
            "no active connector_operational_policy; load connector_control "
            "before proposing a Gmail ingestion plan"
        )
    ceiling_items = int(policy.data.get("max_acquisition_items") or 250)
    ceiling_pages = int(policy.data.get("max_acquisition_pages") or 10)
    max_items = min(configured.default_max_messages, ceiling_items)
    max_pages = min(configured.default_max_pages, ceiling_pages)
    window, derivation = derive_gmail_window(
        topology,
        window_days=int(window_days or configured.default_window_days),
        max_items=max_items,
        policy_id=str(policy.data.get("policy_identity") or ""),
        policy_version=int(policy.data.get("version") or 1),
        provenance=provenance,
    )
    return propose_ingestion_plan_fn(
        graph,
        source_surface_id=source_surface_id,
        service="gmail",
        account_ref=account_ref,
        family="conversation",
        window=window,
        derivation=derivation,
        surfaces=_plan_surfaces(topology, signal_map),
        caps={
            "max_items": max_items,
            "max_pages": max_pages,
            "page_size": configured.default_page_size,
        },
        interpretation_stages=list(GMAIL_INTERPRETATION_STAGES),
        purpose=purpose,
        proposed_by=GMAIL_PLAN_PROPOSER,
        metadata={"integration_profile_id": profile.id if profile else None},
        reader=reader,
    )


def _gmail_plan_executor(graph, plan) -> dict[str, Any]:
    """Execute an approved plan: resolve provider context and start the run."""
    from .tools import request_gmail_backfill_fn

    data = plan.data or {}
    surface_id = str(data.get("source_surface_id") or "")
    request = next(
        (
            obj for obj in graph.objects(type="source_connection_request")
            if obj.data.get("surface_id") == surface_id
            and (obj.data.get("provider") or {}).get("service") == "gmail"
        ),
        None,
    )
    if request is None:
        raise ValueError(
            f"no Gmail source_connection_request records provider context for "
            f"surface {surface_id!r}; re-run exploration before executing"
        )
    provider = dict(request.data.get("provider") or {})
    user_id = str((request.data.get("metadata") or {}).get("user_id") or "")
    if not user_id:
        raise ValueError(
            "the recorded connection request predates plan execution and "
            "carries no user_id; re-run exploration to refresh it"
        )
    return request_gmail_backfill_fn(
        graph,
        source_surface_id=surface_id,
        account_ref=str(data.get("account_ref") or ""),
        user_id=user_id,
        connected_account_id=str(provider.get("connected_account_id") or ""),
        route=str(provider.get("route") or "composio"),
        plan_ref=str(data.get("plan_identity") or ""),
    )


def prepare_gmail_plan_execution(graph, plan) -> dict[str, Any]:
    """Prepare Gmail run creation without contacting Gmail.

    Gmail provider calls already ride the tool-gateway durable-attempt seam.
    This plan-level deferred seam therefore prepares the provider context and
    lets the commit phase create the bounded run plus its first capability
    call.  The plan attempt and the later capability attempt are both durable:
    a restart can neither forget an approved plan nor repeat a delivered
    provider outcome.

    Missing recorded provider context is returned as data, not raised out of
    the host pump.  Commit turns it into a durable abandonment with the exact
    reason so a stale approved plan can never gate onboarding forever.
    """
    data = plan.data or {}
    surface_id = str(data.get("source_surface_id") or "")
    request = next(
        (
            obj for obj in graph.objects(type="source_connection_request")
            if obj.data.get("surface_id") == surface_id
            and (obj.data.get("provider") or {}).get("service") == "gmail"
        ),
        None,
    )
    if request is None:
        return {
            "error": (
                "no Gmail source_connection_request records provider context "
                f"for surface {surface_id!r}; reconnect before retrying"
            ),
        }
    provider = dict(request.data.get("provider") or {})
    user_id = str((request.data.get("metadata") or {}).get("user_id") or "")
    if not user_id:
        return {
            "error": (
                "the recorded Gmail connection carries no user_id; reconnect "
                "to refresh its provider context"
            ),
        }
    return {
        "source_surface_id": surface_id,
        "account_ref": str(data.get("account_ref") or ""),
        "user_id": user_id,
        "connected_account_id": str(provider.get("connected_account_id") or ""),
        "route": str(provider.get("route") or "composio"),
        "plan_identity": str(data.get("plan_identity") or ""),
    }


def perform_prepared_gmail_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Deferred phase 2: deliberately no graph and no provider contact.

    The commit creates a gateway capability call; that capability's own
    perform phase is where Gmail is contacted off the engine thread.  Keeping
    one network seam avoids bypassing gateway authority or duplicating its
    crash-recovery ledger.
    """
    return {"ok": not bool(payload.get("error")), "error": payload.get("error")}


def commit_gmail_plan_execution(
    graph, plan, payload: dict[str, Any], outcome: dict[str, Any]
) -> dict[str, Any]:
    """Create the bounded Gmail run or visibly settle stale approval."""
    error = str(outcome.get("error") or payload.get("error") or "").strip()
    if error:
        from packs.connector_control.plans import abandon_ingestion_plan_fn

        abandon_ingestion_plan_fn(
            graph,
            plan_ref=str((plan.data or {}).get("plan_identity") or plan.id),
            actor="system:gmail.plan_execution",
            reason=error,
        )
        return {"ok": False, "abandoned": True, "error": error}

    from .tools import request_gmail_backfill_fn

    return request_gmail_backfill_fn(
        graph,
        source_surface_id=str(payload.get("source_surface_id") or ""),
        account_ref=str(payload.get("account_ref") or ""),
        user_id=str(payload.get("user_id") or ""),
        connected_account_id=str(payload.get("connected_account_id") or ""),
        route=str(payload.get("route") or "composio"),
        plan_ref=str(payload.get("plan_identity") or ""),
    )


def register_gmail_ingestion_plans() -> None:
    register_ingestion_plan_executor("gmail", _gmail_plan_executor)
    register_deferred_plan_execution(
        "gmail",
        prepare=prepare_gmail_plan_execution,
        perform=perform_prepared_gmail_plan,
        commit=commit_gmail_plan_execution,
        # The perform is a no-network no-op (Gmail is contacted through the
        # gateway capability seam); starting an approved acquisition is
        # owner-visible foreground work and must never queue behind model
        # reasoning.
        work_class="foreground",
    )


def current_gmail_plan_fn(reader, source_surface_id: str):
    return current_plan_for_surface_fn(reader, source_surface_id)


__all__ = [
    "GMAIL_INTERPRETATION_STAGES",
    "GMAIL_PLAN_PROPOSER",
    "current_gmail_plan_fn",
    "derive_gmail_window",
    "plan_backfill_query",
    "commit_gmail_plan_execution",
    "perform_prepared_gmail_plan",
    "prepare_gmail_plan_execution",
    "propose_gmail_ingestion_plan_fn",
    "register_gmail_ingestion_plans",
]
