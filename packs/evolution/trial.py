"""Stage-3 fork trials (design §3): the candidate runs in an isolated
fork of the parent's history, never in the parent.

Called by the HOST between frames (forking mid-behavior is as unsafe as
promoting mid-behavior), same rule as adopt.process_adoption_tickets.
The fork handle is kept in an in-process registry keyed by proposal id;
a restart between trial and adoption loses it, and the adoption
processor then demands a re-trial. That is the design's conflict/retry
path, not an error.

Honest scope notes (design §3 stage 3): replay re-injects recorded
inputs of the configured types (chat segments in v1); the regression
comparator is candidate-behavior failures plus the candidate's own
fixtures; the fixture gate executes candidate code in a subprocess with
a timeout and a key-stripped environment, AFTER the static gates and
BEFORE any approval, which is threat T5's accepted surface until the
runtime's subprocess-isolation design lands.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone

from .gates import _record
from .materialize import materialize_verified
from .settings import EvolutionSettings

# proposal_id -> fork Runtime, for the adoption step. In-process only.
_TRIAL_FORKS: dict[str, object] = {}


def trial_fork_for(proposal_id: str):
    return _TRIAL_FORKS.get(proposal_id)


def clear_trial_forks() -> None:
    _TRIAL_FORKS.clear()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_fixture_gate(root, settings: EvolutionSettings) -> tuple[bool, str]:
    """The sandbox smoke: the candidate's own fixtures, subprocess, timeout,
    no provider keys in the environment (deterministic = true, enforced)."""
    env = {k: v for k, v in os.environ.items()
           if not k.endswith("_API_KEY") and not k.startswith("ACTIVEGRAPH_")}
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    try:
        proc = subprocess.run(
            [sys.executable, str(root / "fixtures" / "run_fixtures.py")],
            capture_output=True, text=True, env=env,
            timeout=settings.trial_fixture_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return False, "fixture gate timed out (trial budget)"
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr)[-1500:]
        return False, f"fixtures exited {proc.returncode}: {tail}"
    return True, ""


def _replay_segment(fork, parent_rt, records: list[dict]) -> None:
    for data in records:
        fork.graph.add_object(data["type"], data["data"])
        fork.run_until_idle()


def _sweep_replay_residue(fork, pre_objects: set, pre_relations: set) -> dict:
    """The trial cleans its own bench before promote (design §7.3).

    Promote's three-way diff treats every fork-only create as adoptable
    state, so the replayed input copies and everything the candidate
    derived from them would ride the delta into the parent as duplicate
    history. They are measurements, not adoptable state: the parent
    already lived those inputs once. Remove every object and relation
    CREATED in the fork after the candidate loaded; PATCHES to
    pre-existing shared objects stay, deliberately, because they are
    the candidate's claim about desired state and exactly what the
    conflict check and the decision surface's diff counts exist to
    scrutinize."""
    new_objects = [o for o in fork.graph.all_objects()
                   if o.id not in pre_objects]
    new_relations = [r for r in fork.graph.all_relations()
                     if r.id not in pre_relations]
    for obj in new_objects:
        fork.graph.remove_object(obj.id)  # cascades touching relations
    for rel in fork.graph.all_relations():
        if rel.id not in pre_relations:  # created between surviving objects
            fork.graph.remove_relation(rel.id)
    return {"objects": len(new_objects), "relations": len(new_relations)}


def run_trial(parent_rt, proposal_id: str, settings: EvolutionSettings) -> dict:
    """Fork, load, fixture gate, in-sample replay, held-out replay, budget.

    Writes gate_result objects for fixtures/in_sample/held_out and one
    mod_trial; patches the proposal to `trialed` or `rejected`. Returns
    the mod_trial data dict."""
    graph = parent_rt.graph
    proposal = graph.get_object(proposal_id)
    assert proposal is not None and proposal.data.get("status") == "gated", (
        "trial requires a gated proposal")

    files, root, pack = materialize_verified(graph, proposal)

    # Sandbox gate: the candidate's own fixtures, out of process.
    ok, detail = _run_fixture_gate(root, settings)
    _record(graph, proposal_id, "fixtures", "pass" if ok else "fail", detail)
    if not ok:
        graph.patch_object(proposal_id, {"status": "rejected",
                                         "status_note": f"fixtures: {detail[:200]}"})
        return {"verdict": "fail", "gate": "fixtures"}

    # Fork at the parent tip.
    tip = graph.events[-1].id
    fork = parent_rt.fork(at_event=tip, label=f"trial:{proposal_id}")
    fork.load_pack(pack)
    events_before = len(fork.graph.events)
    # The residue baseline: everything that exists after the candidate
    # loads (its own load-time state included) is keepable; everything
    # created from here on is replay scaffolding (design §7.3).
    pre_objects = {o.id for o in fork.graph.all_objects()}
    pre_relations = {r.id for r in fork.graph.all_relations()}

    # Replay segments: recorded inputs split in-sample / held-out. The
    # held-out slice is touched exactly once, here (regimes discipline).
    records = [
        {"type": obj.type, "data": dict(obj.data or {})}
        for obj in graph.all_objects()
        if obj.type in settings.replay_object_types
    ]
    split = max(1, int(len(records) * (1 - settings.heldout_fraction))) if records else 0
    in_sample, held_out = records[:split], records[split:]

    def _failures() -> list[dict]:
        """Candidate-behavior failures from the fork's trace, as dicts.

        failures() yields behavior.failed EVENTS whose payload carries the
        pack-qualified behavior name ('<pack>.<behavior>'), the exception,
        and the full traceback (v1.3 DX surface)."""
        prefix = f"{pack.name}."
        out = []
        for event in fork.trace.failures():
            payload = dict(event.payload or {})
            if str(payload.get("behavior", "")).startswith(prefix):
                out.append(payload)
        return out

    _replay_segment(fork, parent_rt, in_sample)
    in_sample_failures = _failures()
    _record(graph, proposal_id, "in_sample",
            "fail" if in_sample_failures else "pass",
            f"{len(in_sample)} inputs, {len(in_sample_failures)} candidate failures")

    held_out_failures: list[dict] = []
    if not in_sample_failures:
        _replay_segment(fork, parent_rt, held_out)
        held_out_failures = [f for f in _failures() if f not in in_sample_failures]
        _record(graph, proposal_id, "held_out",
                "fail" if held_out_failures else "pass",
                f"{len(held_out)} inputs, {len(held_out_failures)} candidate failures")

    new_events = len(fork.graph.events) - events_before
    over_budget = new_events > settings.trial_max_new_events
    if over_budget:
        _record(graph, proposal_id, "in_sample", "fail",
                f"trial budget exceeded: {new_events} new events")

    failures = in_sample_failures + held_out_failures
    verdict = "pass" if not failures and not over_budget else "fail"

    # A passing fork is promote material: sweep the replay residue so
    # the delta the owner reviews (and adoption applies) carries none
    # of it. Failed forks are discarded whole; no sweep needed.
    residue = {"objects": 0, "relations": 0}
    if verdict == "pass":
        residue = _sweep_replay_residue(fork, pre_objects, pre_relations)

    diff = parent_rt.diff(fork)

    trial = graph.add_object("mod_trial", {
        "proposal_id": proposal_id,
        "fork_run_id": fork.run_id,
        "forked_at_event": tip,
        "eval_summary": {
            "in_sample_inputs": len(in_sample),
            "held_out_inputs": len(held_out),
            "new_events": new_events,
            "fixture_gate": "pass",
            "replay_residue_removed": residue,
        },
        "diff_summary": {"is_identical": bool(getattr(diff, "is_identical", False))},
        "failures": [{k: str(v)[:300] for k, v in f.items()} for f in failures][:20],
        "verdict": verdict,
        "at": _now(),
    })
    try:
        graph.add_relation(proposal_id, trial.id, "trialed_in")
    except Exception:
        pass

    if verdict == "pass":
        _TRIAL_FORKS[proposal_id] = fork
        graph.patch_object(proposal_id, {"status": "trialed", "status_note": ""})
    else:
        graph.patch_object(proposal_id, {
            "status": "rejected",
            "status_note": f"trial failed ({len(failures)} failures, "
                           f"budget_exceeded={over_budget})",
        })
    return dict(trial.data)
