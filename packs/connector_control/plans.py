"""Neutral ingestion-plan lifecycle (ADR 0039 / D059).

Every bounded acquisition binds to a versioned plan that was proposed from
discovered understanding, shown to the owner, editable with supersession
(ADR 0020), and receipted. The proposal records its acceptance prediction
before any verdict exists (ADR 0018). Services derive proposals and register
an executor here; the neutral layer owns versioning, ceilings, verdicts, and
the run binding. Nothing in this module knows a provider field.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Optional

from .object_types import ConnectorIngestionPlan


PLAN_EXECUTABLE_STATUSES = frozenset({"approved"})
PLAN_EDITABLE_STATUSES = frozenset({"proposed", "approved"})
PLAN_OPEN_STATUSES = frozenset({"proposed", "approved", "executing"})
PLAN_VERDICTS = ("approved_as_proposed", "edited", "abandoned")

PlanExecutor = Callable[[Any, Any], dict[str, Any]]
_EXECUTORS: dict[str, PlanExecutor] = {}


def register_ingestion_plan_executor(
    service: str, executor: PlanExecutor, *, replace: bool = True
) -> None:
    key = service.strip().lower()
    if not key:
        raise ValueError("service is required")
    if key in _EXECUTORS and not replace:
        raise ValueError(f"ingestion plan executor already registered for {key!r}")
    _EXECUTORS[key] = executor


def unregister_ingestion_plan_executor(service: str) -> None:
    _EXECUTORS.pop(service.strip().lower(), None)


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()}"


def plan_series_id(
    source_surface_id: str, service: str, account_ref: str, family: str
) -> str:
    return _stable_id(
        "ingestion_plan_series", source_surface_id, service, account_ref, family
    )


def _series_plans(reader, plan_series: str):
    rows = [
        obj for obj in reader.objects(type="connector_ingestion_plan")
        if obj.data.get("plan_series") == plan_series
    ]
    rows.sort(key=lambda obj: int(obj.data.get("version") or 0))
    return rows


def _plan_by_identity(reader, plan_identity: str):
    return next(
        (
            obj for obj in reader.objects(type="connector_ingestion_plan")
            if obj.data.get("plan_identity") == plan_identity
        ),
        None,
    )


def resolve_plan_fn(reader, plan_ref: str):
    """Resolve a plan by object id or plan_identity; None when absent."""
    getter = getattr(reader, "get_object", None)
    if callable(getter):
        try:
            obj = getter(plan_ref)
        except Exception:
            obj = None
        if obj is not None and getattr(obj, "type", None) == "connector_ingestion_plan":
            return obj
    return _plan_by_identity(reader, plan_ref)


def current_plan_for_surface_fn(reader, source_surface_id: str):
    """The head (highest, non-superseded) plan version for a surface, or None."""
    rows = [
        obj for obj in reader.objects(type="connector_ingestion_plan")
        if obj.data.get("source_surface_id") == source_surface_id
        and obj.data.get("status") != "superseded"
    ]
    if not rows:
        return None
    rows.sort(key=lambda obj: int(obj.data.get("version") or 0))
    return rows[-1]


def _active_policy(reader):
    return next(
        (
            obj for obj in reader.objects(type="connector_operational_policy")
            if obj.data.get("status") == "active"
        ),
        None,
    )


def _policy_ceilings(reader) -> dict[str, Any]:
    policy = _active_policy(reader)
    if policy is None:
        raise ValueError(
            "no active connector_operational_policy; load connector_control "
            "before proposing an ingestion plan"
        )
    data = policy.data or {}
    return {
        "policy_id": str(data.get("policy_identity") or ""),
        "policy_version": int(data.get("version") or 1),
        "ceiling_items": int(data.get("max_acquisition_items") or 0),
        "ceiling_pages": int(data.get("max_acquisition_pages") or 0),
    }


def ceiling_escalation_error(
    field: str, requested: int, ceiling: int, policy_id: str, policy_version: int
) -> ValueError:
    return ValueError(
        f"{field}={requested} exceeds the operational policy ceiling "
        f"{ceiling} ({policy_id} v{policy_version}). Lowering bounds is always "
        "allowed; raising this one requires an explicit policy escalation: "
        "supersede the connector_operational_policy artifact with a reviewed "
        "new version, then re-propose the plan."
    )


def _validate_caps_against_policy(caps: dict[str, Any]) -> None:
    for field, ceiling_key in (
        ("max_items", "ceiling_items"),
        ("max_pages", "ceiling_pages"),
    ):
        requested = int(caps.get(field) or 0)
        ceiling = int(caps.get(ceiling_key) or 0)
        if requested > ceiling:
            raise ceiling_escalation_error(
                field, requested, ceiling,
                str(caps.get("policy_id") or ""), int(caps.get("policy_version") or 0),
            )


def _predict_acceptance(
    reader, family: str, extra: Optional[list[tuple[str, str]]] = None
) -> dict[str, Any]:
    """Deterministic acceptance prediction from prior same-family verdicts.

    Recorded at propose time, before any verdict for this version can exist
    (ADR 0018). Laplace-smoothed majority so an empty history is an honest
    coin-flip, never fabricated confidence. ``extra`` carries the verdict the
    calling supersession is itself about to record, so the new version's
    prediction already knows it.
    """
    counts = {verdict: 0 for verdict in PLAN_VERDICTS}
    refs: list[str] = []
    for obj in reader.objects(type="connector_ingestion_plan"):
        if obj.data.get("family") != family:
            continue
        verdict = obj.data.get("verdict")
        if verdict in counts:
            counts[verdict] += 1
            refs.append(str(obj.data.get("plan_identity") or obj.id))
    for verdict, ref in extra or []:
        if verdict in counts and ref not in refs:
            counts[verdict] += 1
            refs.append(ref)
    total = sum(counts.values())
    predicted = max(PLAN_VERDICTS, key=lambda verdict: (counts[verdict],))
    if total == 0:
        predicted = "approved_as_proposed"
    confidence = round(100 * (counts[predicted] + 1) / (total + len(PLAN_VERDICTS)))
    return {
        "predicted_verdict": predicted,
        "predicted_confidence_percent": int(confidence),
        "prediction_basis": {
            "scope": family,
            "prior_verdicts": counts,
            "prior_total": total,
            "prior_plan_refs": refs[-20:],
        },
    }


def _body_hash(window, derivation, surfaces, caps, interpretation_stages) -> str:
    body = {
        "window": window,
        "derivation": derivation,
        "surfaces": surfaces,
        "caps": caps,
        "interpretation_stages": list(interpretation_stages),
    }
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _record_verdict(graph, plan, verdict: str, actor: str) -> None:
    """First owner act on a version is its verdict evidence; never rewritten."""
    if plan.data.get("verdict") is None:
        graph.patch_object(
            plan.id,
            {"verdict": verdict, "verdict_actor": actor},
        )


def propose_ingestion_plan_fn(
    graph,
    *,
    source_surface_id: str,
    service: str,
    account_ref: str,
    family: str,
    window: dict[str, Any],
    derivation: dict[str, Any],
    surfaces: Optional[list[dict[str, Any]]] = None,
    caps: Optional[dict[str, Any]] = None,
    interpretation_stages: Optional[list[str]] = None,
    purpose: str = "initial_backfill",
    proposed_by: str = "service",
    metadata: Optional[dict[str, Any]] = None,
    reader=None,
) -> dict[str, Any]:
    """Create the next plan version for a surface from service-derived inputs.

    Caps default to the active policy ceilings' service-requested values and
    must already respect them; a proposal above the ceiling fails the same
    way an edit does. Re-proposing an identical body while the head is still
    unanswered is idempotent, so re-exploration never churns versions.
    """
    view = reader or graph
    ceilings = _policy_ceilings(view)
    requested = dict(caps or {})
    full_caps = {
        "max_items": int(requested.get("max_items") or ceilings["ceiling_items"]),
        "max_pages": int(requested.get("max_pages") or ceilings["ceiling_pages"]),
        "page_size": requested.get("page_size"),
        **ceilings,
    }
    _validate_caps_against_policy(full_caps)

    series = plan_series_id(source_surface_id, service, account_ref, family)
    existing = _series_plans(view, series)
    head = existing[-1] if existing else None
    body_hash = _body_hash(
        window, derivation, surfaces or [], full_caps, interpretation_stages or []
    )
    if head is not None:
        head_status = head.data.get("status")
        if head_status == "proposed" and (
            (head.data.get("metadata") or {}).get("body_hash") == body_hash
        ):
            return {"ok": True, "created": False, "plan": head}
        if head_status in {"approved", "executing"}:
            raise ValueError(
                f"plan {head.data.get('plan_identity')!r} is {head_status} for "
                f"surface {source_surface_id!r}; a new proposal may only "
                "supersede an unanswered one — edit or abandon the current "
                "plan instead"
            )

    version = (int(head.data.get("version") or 0) + 1) if head is not None else 1
    superseding_unanswered = (
        head is not None
        and head.data.get("status") == "proposed"
        and head.data.get("verdict") is None
    )
    prediction = _predict_acceptance(
        view,
        family,
        extra=(
            [("edited", str(head.data.get("plan_identity")))]
            if superseding_unanswered else None
        ),
    )
    payload = ConnectorIngestionPlan(
        plan_identity=_stable_id("ingestion_plan", series, version),
        plan_series=series,
        version=version,
        status="proposed",
        source_surface_id=source_surface_id,
        service=service,
        account_ref=account_ref,
        family=family,
        purpose=purpose,
        window=window,
        derivation=derivation,
        surfaces=list(surfaces or []),
        caps=full_caps,
        interpretation_stages=list(interpretation_stages or []),
        supersedes=(
            str(head.data.get("plan_identity"))
            if head is not None and head.data.get("status") == "proposed"
            else None
        ),
        proposed_by=proposed_by,
        metadata={**dict(metadata or {}), "body_hash": body_hash},
        **prediction,
    ).model_dump()
    plan = graph.add_object("connector_ingestion_plan", payload)
    if head is not None and head.data.get("status") == "proposed":
        _record_verdict(graph, head, "edited", proposed_by)
        graph.patch_object(
            head.id,
            {"status": "superseded", "superseded_by": payload["plan_identity"]},
        )
    return {"ok": True, "created": True, "plan": plan}


def edit_ingestion_plan_fn(
    graph,
    *,
    plan_ref: str,
    edited_by: str,
    window: Optional[dict[str, Any]] = None,
    caps: Optional[dict[str, Any]] = None,
    surfaces: Optional[list[dict[str, Any]]] = None,
    reader=None,
) -> dict[str, Any]:
    """Owner edit: mint a superseding version (ADR 0020 — edit means replace).

    Lowering any cap is always allowed. Raising a cap above the versioned
    policy ceiling is rejected with the escalation path; the plan is left
    untouched. The edited version records verdict evidence "edited".
    """
    view = reader or graph
    plan = resolve_plan_fn(view, plan_ref)
    if plan is None:
        raise ValueError(f"ingestion plan {plan_ref!r} does not exist")
    data = plan.data or {}
    status = data.get("status")
    if status not in PLAN_EDITABLE_STATUSES:
        raise ValueError(
            f"plan {data.get('plan_identity')!r} is {status}; only "
            f"{sorted(PLAN_EDITABLE_STATUSES)} plans can be edited"
        )
    head = current_plan_for_surface_fn(view, str(data.get("source_surface_id")))
    if head is None or head.id != plan.id:
        raise ValueError(
            f"plan {data.get('plan_identity')!r} is not the current version; "
            "edit the head of the series"
        )

    new_window = {**dict(data.get("window") or {}), **dict(window or {})}
    new_caps = {**dict(data.get("caps") or {}), **dict(caps or {})}
    # Re-anchor ceilings to the active policy so an escalated policy is
    # honored and a stale ceiling cannot smuggle a raise through.
    new_caps.update(_policy_ceilings(view))
    _validate_caps_against_policy(new_caps)
    new_surfaces = (
        [dict(row) for row in surfaces]
        if surfaces is not None
        else [dict(row) for row in (data.get("surfaces") or [])]
    )

    version = int(data.get("version") or 1) + 1
    body_hash = _body_hash(
        new_window, dict(data.get("derivation") or {}), new_surfaces, new_caps,
        list(data.get("interpretation_stages") or []),
    )
    prediction = _predict_acceptance(
        view,
        str(data.get("family")),
        extra=(
            [("edited", str(data.get("plan_identity")))]
            if data.get("verdict") is None else None
        ),
    )
    payload = ConnectorIngestionPlan(
        plan_identity=_stable_id(
            "ingestion_plan", str(data.get("plan_series")), version
        ),
        plan_series=str(data.get("plan_series")),
        version=version,
        status="proposed",
        source_surface_id=str(data.get("source_surface_id")),
        service=str(data.get("service")),
        account_ref=str(data.get("account_ref")),
        family=str(data.get("family")),
        purpose=str(data.get("purpose") or "initial_backfill"),
        window=new_window,
        derivation=dict(data.get("derivation") or {}),
        surfaces=new_surfaces,
        caps=new_caps,
        interpretation_stages=list(data.get("interpretation_stages") or []),
        supersedes=str(data.get("plan_identity")),
        proposed_by=edited_by,
        metadata={
            **{
                key: value
                for key, value in dict(data.get("metadata") or {}).items()
                if key != "body_hash"
            },
            "body_hash": body_hash,
            "edit_provenance": {"edited_by": edited_by, "edited_from": data.get("plan_identity")},
        },
        **prediction,
    ).model_dump()
    superseding = graph.add_object("connector_ingestion_plan", payload)
    _record_verdict(graph, plan, "edited", edited_by)
    graph.patch_object(
        plan.id,
        {"status": "superseded", "superseded_by": payload["plan_identity"]},
        rationale=f"superseded by owner edit from {edited_by}",
    )
    return {"ok": True, "created": True, "plan": superseding, "superseded": plan.id}


def approve_ingestion_plan_fn(
    graph, *, plan_ref: str, approved_by: str, reader=None
) -> dict[str, Any]:
    view = reader or graph
    plan = resolve_plan_fn(view, plan_ref)
    if plan is None:
        raise ValueError(f"ingestion plan {plan_ref!r} does not exist")
    data = plan.data or {}
    if data.get("status") != "proposed":
        raise ValueError(
            f"plan {data.get('plan_identity')!r} is {data.get('status')!r}; "
            "only a proposed plan can be approved"
        )
    head = current_plan_for_surface_fn(view, str(data.get("source_surface_id")))
    if head is None or head.id != plan.id:
        raise ValueError(
            f"plan {data.get('plan_identity')!r} was superseded; approve the "
            "current version"
        )
    _record_verdict(graph, plan, "approved_as_proposed", approved_by)
    graph.patch_object(
        plan.id,
        {"status": "approved", "approved_by": approved_by},
        rationale="owner approved the ingestion plan",
    )
    return {"ok": True, "plan": graph.get_object(plan.id)}


def abandon_ingestion_plan_fn(
    graph, *, plan_ref: str, actor: str, reason: str = "", reader=None
) -> dict[str, Any]:
    view = reader or graph
    plan = resolve_plan_fn(view, plan_ref)
    if plan is None:
        raise ValueError(f"ingestion plan {plan_ref!r} does not exist")
    data = plan.data or {}
    if data.get("status") not in PLAN_EDITABLE_STATUSES:
        raise ValueError(
            f"plan {data.get('plan_identity')!r} is {data.get('status')!r}; "
            "only a proposed or approved plan can be abandoned"
        )
    _record_verdict(graph, plan, "abandoned", actor)
    graph.patch_object(
        plan.id,
        {
            "status": "abandoned",
            "metadata": {**dict(data.get("metadata") or {}), "abandon_reason": reason[:500]},
        },
        rationale="owner abandoned the ingestion plan",
    )
    return {"ok": True, "plan": graph.get_object(plan.id)}


def bind_plan_execution_fn(
    graph, *, plan_ref: str, domain_run_id: str, source_surface_id: str, reader=None
) -> dict[str, Any]:
    """Bind an acquisition run to the exact approved plan version it executes.

    Fails loud for a missing, superseded, unapproved, or wrong-surface plan —
    a run without a current approved plan must not exist (ADR 0039).
    """
    view = reader or graph
    plan = resolve_plan_fn(view, plan_ref)
    if plan is None:
        raise ValueError(f"ingestion plan {plan_ref!r} does not exist")
    data = plan.data or {}
    identity = data.get("plan_identity")
    if data.get("source_surface_id") != source_surface_id:
        raise ValueError(
            f"plan {identity!r} belongs to surface "
            f"{data.get('source_surface_id')!r}, not {source_surface_id!r}"
        )
    status = data.get("status")
    if status == "superseded":
        raise ValueError(
            f"plan {identity!r} was superseded by "
            f"{data.get('superseded_by')!r}; a superseded plan can never execute"
        )
    if status == "executing" and data.get("domain_run_id") == domain_run_id:
        return {"ok": True, "plan": plan, "rebound": False}
    if status not in PLAN_EXECUTABLE_STATUSES:
        raise ValueError(
            f"plan {identity!r} is {status!r}; only an approved current plan "
            "can execute"
        )
    graph.patch_object(
        plan.id,
        {"status": "executing", "domain_run_id": domain_run_id},
        rationale="acquisition run bound to the approved ingestion plan",
    )
    return {"ok": True, "plan": graph.get_object(plan.id), "rebound": False}


def execute_ingestion_plan_fn(
    graph, *, plan_ref: str, executed_by: str = "owner", reader=None
) -> dict[str, Any]:
    """Dispatch the approved current plan to its service's registered executor."""
    view = reader or graph
    plan = resolve_plan_fn(view, plan_ref)
    if plan is None:
        raise ValueError(f"ingestion plan {plan_ref!r} does not exist")
    data = plan.data or {}
    status = data.get("status")
    if status == "executing":
        return {
            "ok": True, "already_executing": True,
            "domain_run_id": data.get("domain_run_id"), "plan_id": plan.id,
        }
    if status not in PLAN_EXECUTABLE_STATUSES:
        raise ValueError(
            f"plan {data.get('plan_identity')!r} is {status!r}; approve the "
            "current version before executing"
        )
    head = current_plan_for_surface_fn(view, str(data.get("source_surface_id")))
    if head is None or head.id != plan.id:
        raise ValueError(
            f"plan {data.get('plan_identity')!r} is not current; a superseded "
            "plan can never execute"
        )
    executor = _EXECUTORS.get(str(data.get("service") or "").lower())
    if executor is None:
        return {
            "ok": False, "plan_id": plan.id,
            "error": "ingestion_plan_executor_unavailable",
        }
    result = executor(graph, plan)
    domain_run_id = str(
        result.get("run_id") or result.get("domain_run_id") or ""
    ) or None
    current = graph.get_object(plan.id)
    if domain_run_id and current.data.get("status") != "executing":
        bind_plan_execution_fn(
            graph,
            plan_ref=str(data.get("plan_identity")),
            domain_run_id=domain_run_id,
            source_surface_id=str(data.get("source_surface_id")),
            reader=reader,
        )
    return {
        "ok": True, "plan_id": plan.id,
        "domain_run_id": domain_run_id, "service_result": result,
    }


def settle_plan_for_run_fn(graph, *, domain_run_id: str, state: str, reader=None) -> Optional[str]:
    """Move an executing plan to fulfilled/approved when its bound run ends.

    Neutral: any service's terminal run observation settles its plan without
    family-specific code. Failed runs release the binding back to approved so
    an explicit retry can re-bind; success/partial fulfills the plan.
    """
    view = reader or graph
    plan = next(
        (
            obj for obj in view.objects(type="connector_ingestion_plan")
            if obj.data.get("domain_run_id") == domain_run_id
            and obj.data.get("status") == "executing"
        ),
        None,
    )
    if plan is None:
        return None
    if state in {"succeeded", "partial"}:
        graph.patch_object(plan.id, {"status": "fulfilled"})
        return "fulfilled"
    if state == "failed":
        graph.patch_object(
            plan.id,
            {
                "status": "approved",
                "domain_run_id": None,
                "metadata": {
                    **dict(plan.data.get("metadata") or {}),
                    "released_after_failed_run": domain_run_id,
                },
            },
        )
        return "approved"
    return None


def plan_outcome_fn(plan_data: dict[str, Any], counts: dict[str, Any]) -> dict[str, Any]:
    """Planned-vs-actual payload for the learning delta (counts are actuals)."""
    caps = dict(plan_data.get("caps") or {})
    window = dict(plan_data.get("window") or {})
    imported = int(counts.get("imported") or 0)
    pages = int(counts.get("pages") or 0)
    return {
        "plan_identity": plan_data.get("plan_identity"),
        "plan_version": int(plan_data.get("version") or 0),
        "planned": {
            "max_items": int(caps.get("max_items") or 0),
            "max_pages": int(caps.get("max_pages") or 0),
            "window_days": window.get("days"),
            "estimated_items": window.get("estimated_items"),
        },
        "actual": {"imported": imported, "pages": pages},
        "within_bounds": (
            imported <= int(caps.get("max_items") or 0)
            and pages <= int(caps.get("max_pages") or 0)
        ),
    }


def project_ingestion_plans_fn(graph) -> dict[str, Any]:
    from .tools import CONTROL_CONTRACT_VERSION

    rows = sorted(
        (dict(obj.data) | {"plan_object_id": obj.id}
         for obj in graph.objects(type="connector_ingestion_plan")),
        key=lambda row: (
            row["service"], row["source_surface_id"],
            row["plan_series"], int(row.get("version") or 0),
        ),
    )
    return {"contract_version": CONTROL_CONTRACT_VERSION, "plans": rows}


__all__ = [
    "PLAN_EXECUTABLE_STATUSES",
    "PLAN_EDITABLE_STATUSES",
    "PLAN_OPEN_STATUSES",
    "PLAN_VERDICTS",
    "abandon_ingestion_plan_fn",
    "approve_ingestion_plan_fn",
    "bind_plan_execution_fn",
    "ceiling_escalation_error",
    "current_plan_for_surface_fn",
    "edit_ingestion_plan_fn",
    "execute_ingestion_plan_fn",
    "plan_outcome_fn",
    "plan_series_id",
    "project_ingestion_plans_fn",
    "propose_ingestion_plan_fn",
    "register_ingestion_plan_executor",
    "resolve_plan_fn",
    "settle_plan_for_run_fn",
    "unregister_ingestion_plan_executor",
]
