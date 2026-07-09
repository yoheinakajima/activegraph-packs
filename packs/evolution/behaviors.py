"""Evolution Pack behaviors.

Four reactive pieces of the loop live as behaviors; everything that
must run outside a frame (trials, adoption phase two) lives in trial.py
and adopt.py and is invoked by the host. Everything here no-ops when
EvolutionSettings.enabled is False (the shipped default).

1. gap_detector        — repeated capability failures open a gap, with
                         deterministic taint inheritance.
2. proposal_gatekeeper — mod_proposal.created runs the stage-2 static
                         gates (after the taint check: suspended
                         proposals are never gated).
3. promotion_recorder  — a post-adoption reaction point, on the
                         promote.applied marker (quiescent apply,
                         CONTRACT v1.3 #4): flips mod_promotion from
                         loading to active and the proposal to promoted.
4. watch_monitor       — post-adoption self-noticing (design §3 stage
                         6): scans the event log for behavior.failed
                         events attributable to an adopted pack within
                         its watch window and raises a reflection gap.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from activegraph.packs import behavior

from .settings import EvolutionSettings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evt_ord(evt_id: object) -> Optional[int]:
    """Numeric ordinal of an ``evt_NNN`` event id, or None if unparseable.

    Event ids are monotonic within a run (``evt_001``, ``evt_002``, ...),
    so the difference between two ids is the number of events between
    them. After a restart the ids reset, so a marker from a previous run
    yields no in-window match — the acute watch window is over by then,
    which is the honest post-restart posture."""
    if not isinstance(evt_id, str) or "_" not in evt_id:
        return None
    tail = evt_id.rsplit("_", 1)[1]
    return int(tail) if tail.isdigit() else None


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


@behavior(
    name="watch_monitor",
    on=["object.created"],
    creates=["capability_gap"],
)
def watch_monitor(event, graph, ctx, *, settings: EvolutionSettings):
    """Post-adoption self-noticing (design §3 stage 6).

    The design calls for "a behavior that watches behavior.failed events
    from the candidate's behaviors." The runtime deliberately suppresses
    behavior.* events from behavior re-matching (loop prevention,
    runtime._on_event), so a behavior cannot subscribe to behavior.failed
    directly. Instead this reacts to ordinary graph activity
    (object.created) and scans the event log for behavior.failed events
    attributable to an adopted pack's own behaviors within
    watch_window_events after its promote marker, raising ONE reflection
    capability_gap per such pack. Self-noticing, not self-healing: the
    fix is a new proposal through the same loop. Scoped to adopted packs
    via the mod_promotion records (which record each adopted pack's
    behavior names at load time), so unrelated behavior failures never
    trigger it.
    """
    if not settings.enabled:
        return
    window = settings.watch_window_events

    # Active adopted packs still identifiable by their promote marker,
    # keyed by the behavior names they registered (recorded at adoption).
    watched: dict[str, list[tuple[str, int]]] = {}
    promo_by_id: dict[str, tuple[object, str]] = {}
    for promo in ctx.view.objects(type="mod_promotion"):
        data = promo.data or {}
        if data.get("status") != "active":
            continue
        marker_ord = _evt_ord(data.get("promote_marker_event_id", ""))
        if marker_ord is None:
            continue
        promo_id = str(promo.id)
        promo_by_id[promo_id] = (promo, data.get("pack_name", ""))
        for bname in (data.get("metadata") or {}).get("behaviors") or []:
            if bname:
                watched.setdefault(bname, []).append((promo_id, marker_ord))
    if not watched:
        return

    # A promotion that already carries an open reflection gap needs no
    # re-notice (one gap per adopted pack); early-out keeps steady state
    # cheap so this doesn't rescan the log on every object.created.
    already = {
        (g.data or {}).get("metadata", {}).get("promotion_id")
        for g in ctx.view.objects(type="capability_gap")
        if (g.data or {}).get("kind") == "reflection"
        and (g.data or {}).get("status") == "open"
    }
    pending = {pid for pid in promo_by_id if pid not in already}
    if not pending:
        return

    # Scan the log for attributable in-window failures. Reverse-iterate
    # and stop once past the earliest marker: an event older than every
    # marker cannot be inside any window.
    min_marker = min(m for lst in watched.values() for (_, m) in lst)
    counts: dict[str, int] = {}
    for ev in reversed(list(ctx.view.events())):
        f_ord = _evt_ord(getattr(ev, "id", ""))
        if f_ord is not None and f_ord <= min_marker:
            break
        if ev.type != "behavior.failed" or f_ord is None:
            continue
        bname = (ev.payload or {}).get("behavior", "")
        for (pid, marker_ord) in watched.get(bname, ()):
            if pid in pending and marker_ord < f_ord <= marker_ord + window:
                counts[pid] = counts.get(pid, 0) + 1

    for pid, count in counts.items():
        _promo, pack_name = promo_by_id[pid]
        graph.add_object("capability_gap", {
            "kind": "reflection",
            "description": (
                f"adopted pack {pack_name!r} produced {count} behavior "
                "failure(s) within the post-adoption watch window"),
            "evidence_refs": [],
            # A behavior.failed event carries no injected content, and a
            # promoted pack's proposal already cleared the taint gate, so
            # there is nothing to inherit.
            "injection_flags": [],
            "status": "open",
            "metadata": {"promotion_id": pid, "pack_name": pack_name,
                         "watch_failures": count, "source": "watch_monitor"},
        })


BEHAVIORS = [gap_detector, proposal_gatekeeper, promotion_recorder, watch_monitor]
