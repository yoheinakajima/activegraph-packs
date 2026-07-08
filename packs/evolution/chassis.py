"""The between-frames evolution sweep the host runs (the demo server's
tick driver, or a fixture): process adoption tickets, then handle the
conflict outcomes with a BOUNDED automatic retry.

A promote conflict is a timing problem, not a code problem: the parent
advanced while the trial fork was in flight. So the chassis may retry
on its own authority: re-gate, re-trial at parent-now, and requeue a
ticket under the SAME approved call. The approval authorized these
exact bytes (the bundle-hash pin), and none of that changed; what
changed is the parent's unrelated state.

The retry is CAPPED (design §3 stage 5, scare-list #5): a proposal
that keeps conflicting is telling you its target state is contested,
and an uncapped chassis would fork, replay, and requeue forever.
After `max_conflict_retries` automatic attempts, the proposal moves to
`needs_owner`, a TERMINAL state: gates, trials, and ticket processing
all refuse it, and only the owner (re-gating it by hand, or denying
it) moves it again. The /approvals index lists parked proposals.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from .adopt import process_adoption_tickets
from .settings import EvolutionSettings
from .trial import run_trial

# Outcomes where an automatic retry is meaningful: the dry-run conflict
# (nothing loaded), the late conflict (pack loaded, state unadopted;
# load_pack is idempotent so the retry is safe), and a lost trial fork
# after a restart.
RETRYABLE_OUTCOMES = ("conflict", "conflict_late", "retrial_required")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sweep_evolution(
    rt, settings: EvolutionSettings, *,
    _before_promote: Optional[Callable] = None,
) -> list[dict]:
    """One chassis sweep: tickets, then capped conflict retries.

    Returns the ticket outcomes, each annotated with a `retry` key when
    the retry logic touched it. A requeued ticket is processed on the
    NEXT sweep, so every attempt is a separate, auditable pass."""
    outcomes = process_adoption_tickets(
        rt, settings, _before_promote=_before_promote)
    graph = rt.graph
    for outcome in outcomes:
        if outcome.get("outcome") not in RETRYABLE_OUTCOMES:
            continue
        ticket = graph.get_object(outcome["ticket"])
        proposal_id = (ticket.data or {}).get("proposal_id", "") if ticket else ""
        proposal = graph.get_object(proposal_id) if proposal_id else None
        if proposal is None:
            continue

        metadata = dict(proposal.data.get("metadata") or {})
        retries = int(metadata.get("auto_retries", 0))
        if retries >= settings.max_conflict_retries:
            graph.patch_object(proposal_id, {
                "status": "needs_owner",
                "status_note": (
                    f"parked after {retries} automatic conflict "
                    f"retr{'y' if retries == 1 else 'ies'} "
                    f"(cap {settings.max_conflict_retries}); the target "
                    "state is contested and needs the owner"),
            })
            outcome["retry"] = "needs_owner"
            continue

        metadata["auto_retries"] = retries + 1
        graph.patch_object(proposal_id, {
            "status": "gated", "metadata": metadata,
            "status_note": f"auto retry {retries + 1} of "
                           f"{settings.max_conflict_retries}",
        })
        trial = run_trial(rt, proposal_id, settings)
        if trial.get("verdict") != "pass":
            # run_trial already moved the proposal to rejected with the
            # evidence; the retry loop is done with it.
            outcome["retry"] = "retrial_failed"
            continue
        graph.add_object("adoption_ticket", {
            "kind": "adopt",
            "proposal_id": proposal_id,
            "call_id": (ticket.data or {}).get("call_id", ""),
            "reason": (f"automatic conflict retry {retries + 1} of "
                       f"{settings.max_conflict_retries} (same approved "
                       "call; the bundle-hash pin is unchanged)"),
            "status": "open",
            "at": _now(),
            "metadata": {"retry_of": str(ticket.id),
                         "auto_retry": retries + 1},
        })
        outcome["retry"] = (f"requeued ({retries + 1}/"
                            f"{settings.max_conflict_retries})")
    return outcomes
