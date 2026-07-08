"""Stage-3 fork trials on the runtime's subprocess primitive
(activegraph.sandbox.run_forked_trial, CONTRACT v1.5 #1).

ALL candidate execution happens in a fresh-interpreter child now: the
fixture gate, the in-sample replay, and the held-out replay. The parent
never imports candidate code at trial time (the import happens at
ADOPTION, after gates re-run and a verified approval). What the child
enforces before any import: the bundle-hash pin and the manifest chain,
so the gate-time hash check has a process boundary behind it.

Two child runs per trial, both key-free by construction (the child
configures no LLM provider; the environment is allow-list only):

  1. The candidate's own fixtures (`fixtures/run_fixtures.py::main`),
     the cheap smoke, under the fixture wall clock.
  2. The chassis trial driver (`fixtures/trial_scenario.py::main`, a
     pinned, gate-verified file: see trial_driver.py), which replays
     the recorded segment in-sample then held-out, sweeps the replay
     residue (design §7.3), and leaves untyped trial_stage_result
     markers in the fork.

The store is the record: the parent reads verdict evidence from the
fork's run after the child exits (markers, trace failures, event
counts), removes the markers so the promote delta stays clean, and
writes gate_result / mod_trial objects in its own graph. Budgets map
onto the runtime's three nets (rlimits + wall-clock kill + event
budget); the parent double-enforces nothing.

Called by the HOST between frames, same rule as
adopt.process_adoption_tickets. The fork persists in the store, so a
restart between trial and adoption no longer forces a re-trial: the
adoption processor reloads the fork by run id.
"""

from __future__ import annotations

from datetime import datetime, timezone

from activegraph.packs.manifest import verify_bundle_hash

from .gates import _record
from .materialize import proposal_files, write_files
from .settings import EvolutionSettings
from .trial_driver import TRIAL_DRIVER_PATH


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _store_path(rt) -> str:
    path = getattr(getattr(rt.graph, "store", None), "path", None)
    assert path, "fork trials require a SQLite-backed runtime (persist_to=...)"
    return str(path)


def load_trial_fork(rt, fork_run_id: str):
    """Reload a trial fork from the store (behaviors off: the parent
    reads and promotes it, nothing should fire). Returns None when the
    run is unloadable or empty (e.g. retired)."""
    from activegraph.runtime.runtime import Runtime

    if not fork_run_id:
        return None
    try:
        fork = Runtime.load(_store_path(rt), run_id=fork_run_id, behaviors=[])
    except Exception:
        return None
    if not fork.graph.events:
        return None
    return fork


def run_trial(parent_rt, proposal_id: str, settings: EvolutionSettings) -> dict:
    """Materialize (pin-first), fixture-gate child, replay child, read
    the store, record verdicts.

    Writes gate_result objects for fixtures/in_sample/held_out and one
    mod_trial; patches the proposal to `trialed` or `rejected`. Returns
    the mod_trial data dict (or a short failure dict when the fixture
    gate stops the trial)."""
    from activegraph.sandbox import PackSource, TrialLimits, run_forked_trial

    graph = parent_rt.graph
    proposal = graph.get_object(proposal_id)
    assert proposal is not None and proposal.data.get("status") == "gated", (
        "trial requires a gated proposal")

    # Materialization is pin-first and import-free in the parent: bytes
    # to disk, bundle hash verified; the child verifies again before it
    # imports anything.
    files = proposal_files(graph, proposal)
    root = write_files(files, pack_name=proposal.data["pack_name"])
    verify_bundle_hash(proposal.data.get("bundle_hash", ""), root)

    store_path = _store_path(parent_rt)
    tip = graph.events[-1].id
    source = PackSource(root_dir=str(root),
                        expected_bundle_hash=proposal.data.get("bundle_hash", ""))

    # Child run 1: the candidate's own fixtures (sandbox smoke).
    fixture_report = run_forked_trial(
        store_path,
        parent_run_id=parent_rt.run_id,
        at_event=tip,
        pack_source=source,
        scenario="fixtures/run_fixtures.py::main",
        limits=TrialLimits(
            wall_clock_seconds=settings.trial_fixture_timeout_seconds,
            max_rss_bytes=settings.trial_max_rss_bytes,
            max_events=settings.trial_max_new_events,
        ),
        label=f"evolution-fixturegate:{proposal_id}",
    )
    fixtures_ok = (fixture_report.outcome == "completed"
                   and fixture_report.behavior_failures == 0)
    _record(graph, proposal_id, "fixtures",
            "pass" if fixtures_ok else "fail",
            f"{fixture_report.outcome}"
            + (f": {fixture_report.detail}" if fixture_report.detail else ""))
    if not fixtures_ok:
        graph.patch_object(proposal_id, {
            "status": "rejected",
            "status_note": (f"fixtures: {fixture_report.outcome} "
                            f"{fixture_report.detail}")[:200],
        })
        return {"verdict": "fail", "gate": "fixtures",
                "outcome": fixture_report.outcome}

    # Child run 2: the pinned chassis driver replays the recorded
    # segment (in-sample, then held-out exactly once) and sweeps.
    report = run_forked_trial(
        store_path,
        parent_run_id=parent_rt.run_id,
        at_event=tip,
        pack_source=source,
        scenario=f"{TRIAL_DRIVER_PATH}::main",
        limits=TrialLimits(
            wall_clock_seconds=settings.trial_wall_clock_seconds,
            max_rss_bytes=settings.trial_max_rss_bytes,
            max_events=settings.trial_max_new_events,
        ),
        label=f"evolution-trial:{proposal_id}",
    )

    # The store is the record: read stages, failures, and the sweep
    # from the fork's run, then remove the markers so the promote delta
    # carries no trial bookkeeping.
    fork = load_trial_fork(parent_rt, report.fork_run_id)
    stages: dict[str, dict] = {}
    failures: list[dict] = []
    if fork is not None:
        markers = [o for o in fork.graph.all_objects()
                   if o.type == "trial_stage_result"]
        stages = {str(m.data.get("stage", "")): dict(m.data or {})
                  for m in markers}
        prefix = f"{proposal.data['pack_name']}."
        for event in fork.trace.failures():
            payload = dict(event.payload or {})
            if str(payload.get("behavior", "")).startswith(prefix):
                failures.append(payload)
        for m in markers:
            fork.graph.remove_object(m.id)

    for stage_name in ("in_sample", "held_out"):
        stage = stages.get(stage_name)
        if stage is None:
            continue
        stage_failures = int(stage.get("failures", 0))
        _record(graph, proposal_id, stage_name,
                "fail" if stage_failures else "pass",
                f"{stage.get('inputs', 0)} inputs, "
                f"{stage_failures} candidate failures")
    if report.outcome == "limits_exceeded":
        _record(graph, proposal_id, "in_sample", "fail",
                f"trial limits: {report.detail}")
    elif report.outcome != "completed" and not stages:
        _record(graph, proposal_id, "in_sample", "fail",
                f"{report.outcome}: {report.detail}")

    stage_failures = sum(int(s.get("failures", 0)) for s in stages.values()
                         if "failures" in s)
    verdict = ("pass" if report.outcome == "completed"
               and report.behavior_failures == 0
               and stage_failures == 0 else "fail")

    diff = parent_rt.diff(fork) if fork is not None else None
    sweep = stages.get("sweep", {})

    trial = graph.add_object("mod_trial", {
        "proposal_id": proposal_id,
        "fork_run_id": report.fork_run_id,
        "forked_at_event": tip,
        "eval_summary": {
            "in_sample_inputs": int(stages.get("in_sample", {}).get("inputs", 0)),
            "held_out_inputs": int(stages.get("held_out", {}).get("inputs", 0)),
            "new_events": report.events_appended,
            "fixture_gate": "pass",
            "child_outcome": report.outcome,
            "replay_residue_removed": {
                "objects": int(sweep.get("objects", 0)),
                "relations": int(sweep.get("relations", 0)),
            },
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
        graph.patch_object(proposal_id, {"status": "trialed", "status_note": ""})
    else:
        graph.patch_object(proposal_id, {
            "status": "rejected",
            "status_note": (f"trial failed (outcome={report.outcome}, "
                            f"{len(failures)} candidate failures)")[:200],
        })
    return dict(trial.data)
