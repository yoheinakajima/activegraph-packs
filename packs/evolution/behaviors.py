"""Evolution Pack behaviors.

Three reactive pieces of the loop live as behaviors; everything that
must run outside a frame (trials, adoption phase two) lives in trial.py
and adopt.py and is invoked by the host. Everything here no-ops when
EvolutionSettings.enabled is False (the shipped default).

1. gap_detector        — repeated capability failures open a gap, with
                         deterministic taint inheritance.
2. proposal_gatekeeper — mod_proposal.created runs the stage-2 static
                         gates (after the taint check: suspended
                         proposals are never gated).
3. promotion_recorder  — the ONLY post-adoption reaction point, on the
                         promote.applied marker (quiescent apply,
                         CONTRACT v1.3 #4): flips mod_promotion from
                         loading to active and the proposal to promoted.
"""

from __future__ import annotations

from datetime import datetime, timezone

from activegraph.packs import behavior

from .settings import EvolutionSettings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@behavior(
    name="gap_detector",
    on=["object.created"],
    where={"object.type": "capability_result"},
    creates=["capability_gap"],
)
def gap_detector(event, graph, ctx, *, settings: EvolutionSettings):
    """Open a tool_failure gap after N failures of the same capability.

    Taint inheritance is deterministic (design §3 stage 0): the gap's
    injection_flags are the union of flags on the evidence results,
    computed here, never trusted from any model output.
    """
    if not settings.enabled:
        return
    obj = event.payload.get("object", {})
    data = obj.get("data", {})
    if data.get("success", True):
        return
    capability = f"{data.get('provider_name', '')}.{data.get('capability_name', '')}"

    failures = [
        o for o in ctx.view.objects(type="capability_result")
        if not (o.data or {}).get("success", True)
        and f"{o.data.get('provider_name', '')}.{o.data.get('capability_name', '')}" == capability
    ]
    if len(failures) < settings.gap_failure_threshold:
        return
    # One open gap per failing capability.
    for gap in ctx.view.objects(type="capability_gap"):
        if (gap.data or {}).get("status") == "open" and \
                (gap.data or {}).get("metadata", {}).get("capability") == capability:
            return

    inherited = sorted({flag for o in failures
                        for flag in (o.data or {}).get("injection_flags", [])})
    graph.add_object("capability_gap", {
        "kind": "tool_failure",
        "description": f"{capability} failed {len(failures)} times",
        "evidence_refs": [str(o.id) for o in failures],
        "injection_flags": inherited,
        "status": "open",
        "metadata": {"capability": capability},
    })


@behavior(
    name="proposal_gatekeeper",
    on=["object.created"],
    where={"object.type": "mod_proposal"},
    creates=["gate_result"],
)
def proposal_gatekeeper(event, graph, ctx, *, settings: EvolutionSettings):
    """Run the static gates on every new proposal.

    Taint first (design §6 T2): a proposal whose own flags, or whose
    gap's inherited flags, are non-empty is suspended before any gate
    runs, and stays suspended until the owner explicitly clears it.
    """
    if not settings.enabled:
        return
    obj = event.payload.get("object", {})
    proposal_id = obj.get("id")
    data = obj.get("data", {})
    if not proposal_id or data.get("status") != "drafted":
        return

    flags = list(data.get("injection_flags") or [])
    gap_id = data.get("gap_id", "")
    if gap_id:
        gap = graph.get_object(gap_id)
        if gap is not None:
            flags += list((gap.data or {}).get("injection_flags") or [])
    if flags:
        graph.patch_object(proposal_id, {
            "status": "suspended",
            "status_note": f"tainted lineage: {', '.join(sorted(set(flags)))}",
            "injection_flags": sorted(set(flags)),
        })
        return

    from .gates import run_static_gates
    proposal = graph.get_object(proposal_id)
    run_static_gates(graph, proposal, settings)


@behavior(
    name="promotion_recorder",
    on=["promote.applied"],
)
def promotion_recorder(event, graph, ctx, *, settings: EvolutionSettings):
    """Flip the loading mod_promotion to active on the marker event.

    Quiescent apply means this is the one and only reaction point: the
    delta events fired nothing, by design. Matching is by the marker's
    from_run against the promotion's recorded fork run id.
    """
    payload = event.payload or {}
    from_run = payload.get("from_run", "")
    if not from_run:
        return
    for promotion in ctx.view.objects(type="mod_promotion"):
        data = promotion.data or {}
        if data.get("fork_run_id") == from_run and data.get("status") == "loading":
            graph.patch_object(promotion.id, {
                "status": "active",
                "promote_marker_event_id": str(getattr(event, "id", "")),
                "applied_counts": {
                    "objects_created": len(payload.get("objects_created", [])),
                    "objects_patched": len(payload.get("objects_patched", [])),
                    "objects_removed": len(payload.get("objects_removed", [])),
                    "relations_created": len(payload.get("relations_created", [])),
                    "relations_removed": len(payload.get("relations_removed", [])),
                },
                "at": _now(),
            })
            proposal_id = data.get("proposal_id", "")
            if proposal_id and graph.get_object(proposal_id) is not None:
                graph.patch_object(proposal_id, {"status": "promoted",
                                                 "status_note": ""})
            return


BEHAVIORS = [gap_detector, proposal_gatekeeper, promotion_recorder]
