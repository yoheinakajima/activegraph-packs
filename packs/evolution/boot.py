"""Boot-time persistence for adopted packs (design §4), plus retention
housekeeping over trial forks (design §7.5, on the v1.5 runtime API).

`load_pack` is a runtime call, so adopted packs vanish on restart. The
graph is the durable registry: the chassis calls
`reload_adopted_packs(rt)` at boot, which re-materializes every ACTIVE
mod_promotion from its artifacts, re-verifies the bundle-hash pin, and
loads. Disabled and loading-state promotions stay down; a hash mismatch
disables the promotion loudly and opens a capability_gap so the
condition is impossible to miss.

`retire_unpinned_trial_forks` is the cleanup half: sandbox trials leave
fork runs in the store, and the ones that never got promoted are
disposable. Retirement goes THROUGH the runtime's retention API, whose
pin set dominates unconditionally: a promoted-from fork raises
RetentionPinnedError and stays, because it is provenance for adopted
state, never garbage.

Concurrency (CONTRACT v1.5 #2 addendum 2b, ruled per-RUN): "no runtime
attached" scopes to the RUN the operation touches, not the whole file.
Retiring a fork run is safe while a live runtime is attached to OTHER
runs in the same SQLite store (the parent, say); the runtime pins that
pattern with test_retire_fork_per_run_while_parent_runtime_is_live.
WAL, run_id-scoped statements, and one short BEGIN IMMEDIATE archive
transaction make contention resolve as wait-then-succeed or a clean
OperationalError, never corruption. Two conditions this pack keeps:

  1. Never race a pin-creating operation against retirement of the SAME
     run: don't retire a fork with a promote from it in flight. Retire
     only after decisions are final. This pack satisfies it structurally
     by retiring only forks whose proposals are in terminal or
     no-longer-adoptable states (see _FORK_RETAINING_STATUSES, the kept
     set). A lost race degrades an audit walk, never destroys data:
     archiving is a move, and archived rows stay readable via
     iter_archived.
  2. The whole-file caveat still binds the run being operated on ITSELF:
     compact/retire under a runtime attached to that same run is the
     real hazard (snapshot-event id collision, empirically verified).
     This pack never retires a run it holds a live runtime on: the demo
     server runs housekeeping pre-boot, and the reader below loads each
     root run only to inspect it, never to retire it.
"""

from __future__ import annotations

from activegraph.packs.manifest import PackManifestError

from .materialize import materialize_verified

_TRIAL_LABEL_PREFIXES = ("evolution-trial:", "evolution-fixturegate:")

# Proposal statuses whose trial forks are still wanted: mid-flight
# adoptions reload the fork by run id, and a parked needs_owner
# proposal may be re-adopted by the owner without a re-trial.
_FORK_RETAINING_STATUSES = ("trialed", "pending_approval", "adopting",
                            "needs_owner")


def retire_unpinned_trial_forks(store_path: str) -> dict[str, str]:
    """Archive disposable trial-fork runs; report every decision.

    Returns {run_id: outcome} with outcomes 'retired', 'pinned: ...',
    'in-flight (kept)', or 'error: ...'. The pin check is the runtime's
    own (`pins()` dominates any policy here); the in-flight check is
    this pack's: forks whose proposal could still be adopted are kept
    even though nothing pins them yet."""
    from activegraph.runtime.runtime import Runtime
    from activegraph.store.retention import RetentionPinnedError, pins, retire
    from activegraph.store.sqlite import SQLiteEventStore

    # Which proposals still want their forks: read every ROOT run
    # (parentless; Runtime.load with no run_id picks the most recently
    # touched run, which after a trial is a fork), read-only,
    # behaviors off.
    retained_fork_ids: set[str] = set()
    for record in SQLiteEventStore.list_runs(store_path):
        if record.parent_run_id:
            continue
        try:
            view = Runtime.load(store_path, run_id=record.run_id,
                                behaviors=[])
            proposals = {str(p.id): (p.data or {}).get("status", "")
                         for p in view.graph.objects(type="mod_proposal")}
            for t in view.graph.objects(type="mod_trial"):
                status = proposals.get(str(t.data.get("proposal_id", "")), "")
                if status in _FORK_RETAINING_STATUSES:
                    retained_fork_ids.add(str(t.data.get("fork_run_id", "")))
        except Exception:
            continue  # unreadable root: label + pins still protect

    outcomes: dict[str, str] = {}
    for record in SQLiteEventStore.list_runs(store_path):
        label = record.label or ""
        if not label.startswith(_TRIAL_LABEL_PREFIXES):
            continue
        run_id = record.run_id
        if run_id in retained_fork_ids:
            outcomes[run_id] = "in-flight (kept)"
            continue
        reasons = pins(store_path, run_id)
        if reasons:
            outcomes[run_id] = f"pinned: {reasons[0]}"
            continue
        try:
            rows = retire(store_path, run_id)
            outcomes[run_id] = f"retired ({rows} rows archived)"
        except RetentionPinnedError as exc:  # raced pin: report, keep
            outcomes[run_id] = f"pinned: {exc.reasons[0] if exc.reasons else exc}"
        except Exception as exc:
            outcomes[run_id] = f"error: {exc}"
    return outcomes


def reload_adopted_packs(rt) -> dict[str, str]:
    """Returns {pack_name: outcome} for every adoption record seen."""
    graph = rt.graph
    outcomes: dict[str, str] = {}
    for promotion in list(graph.objects(type="mod_promotion")):
        data = promotion.data or {}
        name = data.get("pack_name", "?")
        status = data.get("status")
        if status == "disabled":
            outcomes[name] = "disabled (stays down)"
            continue
        if status != "active":
            outcomes[name] = f"skipped (status={status})"
            continue
        proposal = graph.get_object(data.get("proposal_id", ""))
        if proposal is None:
            outcomes[name] = "skipped (proposal missing)"
            continue
        try:
            _, _, pack = materialize_verified(graph, proposal)
        except PackManifestError as exc:
            graph.patch_object(promotion.id, {"status": "disabled"})
            graph.add_object("capability_gap", {
                "kind": "reflection",
                "description": (f"adopted pack {name!r} failed its bundle-hash "
                                f"pin at boot and was disabled: {exc}"),
                "status": "open",
                "metadata": {"promotion_id": str(promotion.id)},
            })
            print(f"[evolution] BOOT HASH MISMATCH: {name} disabled", flush=True)
            outcomes[name] = "disabled (hash mismatch)"
            continue
        rt.load_pack(pack)
        outcomes[name] = "loaded"
    return outcomes
