"""Stage-4/5 adoption: governed decision, two-phase execution.

Phase one (governed): `evolution.adopt_proposal` and
`evolution.disable_promotion` are gateway capabilities. Registration
REFUSES in configurations where the governance would be theater (design
§3 stage 4, threat T6): a gateway policy that would auto-approve
`critical`, or an identity setup with no verified approver. Their
executors run only after an approved capability call, and all they do is
write an adoption_ticket.

Phase two (chassis): `process_adoption_tickets(rt)` runs between frames
and performs the canonical order: bundle-hash pin, gates re-run, promote
dry-run, load_pack + mod_promotion(loading), real promote. Every abort
leaves graph state saying exactly where and why.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from pydantic import BaseModel, Field

from .gates import run_static_gates
from .materialize import materialize_verified
from .settings import EvolutionSettings
from .trial import load_trial_fork

ADOPT_RISK = "critical"
DISABLE_RISK = "high"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------- registration


def _verified_approver_exists(graph, approver_roles: list[str]) -> tuple[bool, str]:
    try:
        from packs.identity_auth.behaviors import principals_registered
    except Exception:
        return False, "identity_auth is not installed"
    if not principals_registered():
        return False, "no principals registered"
    try:
        for obj in graph.objects(type="principal"):
            if obj.data.get("role") in approver_roles:
                return True, ""
    except Exception:
        return False, "principal type not registered (identity_auth not loaded)"
    return False, f"no principal holds an approver role {approver_roles}"


class AdoptProposalInput(BaseModel):
    proposal_id: str = Field(description="The trialed mod_proposal to adopt.")
    note: str = Field(default="", description="Why adoption is requested.")


class DisablePromotionInput(BaseModel):
    promotion_id: str = Field(description="The mod_promotion to disable.")
    reason: str = Field(default="")


def register_adoption_capabilities(*, gateway_settings, graph) -> None:
    """Register the two governed capabilities, refusing unsafe configs.

    Two refusals, both fixtures-asserted (design §8 item 10):
    auto-approvable critical means the hold is fiction; unverified
    identity means "approved by the owner" is fiction. Unverified-mode
    self-modification must not exist."""
    from packs.tool_gateway.tools import register_local_capability

    if ADOPT_RISK in gateway_settings.auto_approve_risk_classes:
        raise ValueError(
            "evolution.adopt_proposal refuses to register: gateway settings "
            f"auto-approve {ADOPT_RISK!r}, so the owner hold would never "
            "happen. Self-modification without a held approval must not exist."
        )
    ok, reason = _verified_approver_exists(
        graph, list(gateway_settings.approver_roles))
    if not ok:
        raise ValueError(
            "evolution.adopt_proposal refuses to register: approver "
            f"verification is not possible ({reason}). Unverified-mode "
            "self-modification must not exist; load identity_auth and "
            "register an owner principal first."
        )

    def _adopt_executor(proposal_id: str = "", note: str = "",
                        execution_context: Optional[dict] = None) -> dict:
        g = (execution_context or {}).get("graph")
        call_id = (execution_context or {}).get("call_id", "")
        if g is None:
            raise RuntimeError("adopt_proposal requires gateway execution context")
        proposal = g.get_object(proposal_id)
        if proposal is None or proposal.type != "mod_proposal":
            raise RuntimeError(f"no mod_proposal {proposal_id!r}")
        if proposal.data.get("status") not in ("trialed", "pending_approval"):
            raise RuntimeError(
                f"proposal is {proposal.data.get('status')!r}; adoption "
                "requires 'trialed' (or 'pending_approval' when the request "
                "already moved it)")
        ticket = g.add_object("adoption_ticket", {
            "kind": "adopt", "proposal_id": proposal_id, "call_id": str(call_id),
            "reason": note, "status": "open", "at": _now(),
        })
        g.patch_object(proposal_id, {"status": "adopting"})
        return {"ticket_id": str(ticket.id),
                "note": "adoption queued; the chassis applies it between frames"}

    def _disable_executor(promotion_id: str = "", reason: str = "",
                          execution_context: Optional[dict] = None) -> dict:
        g = (execution_context or {}).get("graph")
        call_id = (execution_context or {}).get("call_id", "")
        if g is None:
            raise RuntimeError("disable_promotion requires gateway execution context")
        promotion = g.get_object(promotion_id)
        if promotion is None or promotion.type != "mod_promotion":
            raise RuntimeError(f"no mod_promotion {promotion_id!r}")
        ticket = g.add_object("adoption_ticket", {
            "kind": "disable", "promotion_id": promotion_id,
            "call_id": str(call_id), "reason": reason, "status": "open",
            "at": _now(),
        })
        return {"ticket_id": str(ticket.id),
                "note": "disable queued; the chassis applies it between frames"}

    register_local_capability(
        "evolution", "adopt_proposal", _adopt_executor,
        input_schema=AdoptProposalInput,
        description=("Adopt a gated-and-trialed agent-authored pack: load it "
                     "and promote its trial state. Critical risk: always held "
                     "for a verified owner approval."),
        risk_class=ADOPT_RISK,
    )
    register_local_capability(
        "evolution", "disable_promotion", _disable_executor,
        input_schema=DisablePromotionInput,
        description=("Disable an adopted pack: immediate deregistration plus "
                     "boot-time exclusion."),
        risk_class=DISABLE_RISK,
    )


# ------------------------------------------------------------- phase two


def _abort(graph, ticket, proposal_id: Optional[str], note: str,
           proposal_status: str = "suspended") -> None:
    graph.patch_object(ticket.id, {"status": "aborted", "status_note": note[:500]})
    if proposal_id:
        graph.patch_object(proposal_id, {"status": proposal_status,
                                         "status_note": note[:300]})


def process_adoption_tickets(
    rt, settings: EvolutionSettings, *,
    _before_promote: Optional[Callable] = None,
) -> list[dict]:
    """Apply open tickets, outside any behavior frame (design §3 stage 5).

    `_before_promote` is a test seam: the staleness fixtures use it to
    advance the parent between the dry run and the real promote."""
    from activegraph.packs.manifest import PackManifestError

    graph = rt.graph
    outcomes = []
    tickets = [t for t in graph.objects(type="adoption_ticket")
               if t.data.get("status") == "open"]
    for ticket in tickets:
        kind = ticket.data.get("kind")
        if kind == "disable":
            outcomes.append(_process_disable(rt, graph, ticket))
            continue

        proposal_id = ticket.data.get("proposal_id", "")
        proposal = graph.get_object(proposal_id)
        if proposal is None:
            _abort(graph, ticket, None, "proposal vanished")
            continue
        if proposal.data.get("status") == "needs_owner":
            # Terminal: the retry cap parked it (chassis.py). Nothing
            # automatic resurrects it, tickets included.
            _abort(graph, ticket, None,
                   "proposal is needs_owner (terminal); owner action required")
            outcomes.append({"ticket": ticket.id, "outcome": "needs_owner"})
            continue

        # 1. The bundle-hash pin: what loads is what was reviewed.
        try:
            files, root, pack = materialize_verified(graph, proposal)
        except PackManifestError as exc:
            _abort(graph, ticket, proposal_id,
                   f"bundle-hash pin failed: {exc}")
            outcomes.append({"ticket": ticket.id, "outcome": "hash_mismatch"})
            continue
        except Exception as exc:
            _abort(graph, ticket, proposal_id, f"materialization failed: {exc}")
            outcomes.append({"ticket": ticket.id, "outcome": "error"})
            continue

        # 2. Gates re-run (the world may have changed since gating).
        graph.patch_object(proposal_id, {"status": "gated"})
        if not run_static_gates(graph, graph.get_object(proposal_id), settings):
            graph.patch_object(ticket.id, {"status": "aborted",
                                           "status_note": "gates failed on re-run"})
            outcomes.append({"ticket": ticket.id, "outcome": "gates_failed"})
            continue
        graph.patch_object(proposal_id, {"status": "adopting"})

        # The trial fork persists in the store (v1.5 sandbox trials);
        # reload it by run id from the latest passing trial. Missing or
        # retired fork logs demand a re-trial.
        passing = [t for t in graph.objects(type="mod_trial")
                   if t.data.get("proposal_id") == proposal_id
                   and t.data.get("verdict") == "pass"]
        fork = (load_trial_fork(rt, passing[-1].data.get("fork_run_id", ""))
                if passing else None)
        if fork is None:
            _abort(graph, ticket, proposal_id,
                   "trial fork not available in the store (never trialed, "
                   "or its run was retired); re-trial required",
                   proposal_status="gated")
            outcomes.append({"ticket": ticket.id, "outcome": "retrial_required"})
            continue

        # 3. Promote dry run BEFORE the irreversible load.
        plan = rt.promote(fork, dry_run=True)
        if not plan.is_promotable:
            _abort(graph, ticket, proposal_id,
                   f"promote conflicts: {[str(c) for c in plan.conflicts][:5]}",
                   proposal_status="conflict")
            outcomes.append({"ticket": ticket.id, "outcome": "conflict"})
            continue

        if _before_promote is not None:
            _before_promote()

        # 4. Load, and record the promotion at LOAD TIME (status=loading)
        # so the pack is trackable even if step 5 aborts.
        rt.load_pack(pack)
        promotion = graph.add_object("mod_promotion", {
            "proposal_id": proposal_id,
            "trial_id": "",
            "pack_name": proposal.data.get("pack_name", ""),
            "fork_run_id": getattr(fork, "run_id", ""),
            "bundle_hash": proposal.data.get("bundle_hash", ""),
            "status": "loading",
            "at": _now(),
            # The adopted pack's own behavior names, namespaced exactly as
            # the runtime emits them in a behavior.failed payload
            # ("<pack>.<behavior>"): the attribution key the stage-6
            # watch_monitor uses to tie a failure back to this promotion
            # (design §3 stage 6).
            "metadata": {"behaviors": [
                f"{getattr(pack, 'name', '')}.{getattr(b, 'name', '')}"
                for b in getattr(pack, "behaviors", [])
                if getattr(b, "name", "")]},
        })
        try:
            graph.add_relation(proposal_id, promotion.id, "promoted_as")
        except Exception:
            pass

        # 5. The real promote; apply recomputes against parent-now.
        try:
            result = rt.promote(fork)
        except Exception as exc:
            graph.patch_object(ticket.id, {
                "status": "aborted",
                "status_note": f"promote aborted: {type(exc).__name__}: {exc}"[:500],
            })
            graph.patch_object(proposal_id, {
                "status": "conflict",
                "status_note": "parent advanced between dry run and apply; "
                               "re-fork and re-trial",
            })
            outcomes.append({"ticket": ticket.id, "outcome": "conflict_late",
                             "promotion": promotion.id})
            continue

        graph.patch_object(ticket.id, {"status": "done"})
        outcomes.append({
            "ticket": ticket.id, "outcome": "promoted",
            "promotion": promotion.id,
            "marker_event_id": result.marker_event_id,
        })
        # promotion_recorder (on promote.applied) flips loading -> active
        # on the next settle.
        rt.run_until_idle()
    return outcomes


def _process_disable(rt, graph, ticket) -> dict:
    promotion_id = ticket.data.get("promotion_id", "")
    promotion = graph.get_object(promotion_id)
    if promotion is None:
        _abort(graph, ticket, None, "promotion vanished")
        return {"ticket": ticket.id, "outcome": "error"}
    pack_name = promotion.data.get("pack_name", "")
    deregistered = False
    try:
        deregistered = bool(rt.disable_pack(pack_name))
    except Exception:
        pass  # a loading-state pack may not be registered on THIS runtime
    graph.patch_object(promotion_id, {"status": "disabled"})
    rollback = graph.add_object("mod_rollback", {
        "promotion_id": promotion_id,
        "method": "disable_pack" if deregistered else "boot_exclusion",
        "reason": ticket.data.get("reason", ""),
        "at": _now(),
    })
    try:
        graph.add_relation(promotion_id, rollback.id, "rolled_back_by")
    except Exception:
        pass
    proposal_id = promotion.data.get("proposal_id", "")
    if proposal_id and graph.get_object(proposal_id) is not None:
        graph.patch_object(proposal_id, {"status": "disabled"})
    graph.patch_object(ticket.id, {"status": "done"})
    return {"ticket": ticket.id, "outcome": "disabled",
            "deregistered": deregistered}
