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


def _reload_one(rt, graph, promotion, name: str, *, suffix: str = "") -> str:
    """Materialize, hash-verify, and load ONE promotion's pack. Returns the
    outcome string. A bundle-hash mismatch disables the promotion loudly and
    opens a capability_gap (the pre-existing behavior, unchanged)."""
    proposal = graph.get_object((promotion.data or {}).get("proposal_id", ""))
    if proposal is None:
        return "skipped (proposal missing)" + suffix
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
        return "disabled (hash mismatch)" + suffix
    rt.load_pack(pack)
    return "loaded" + suffix


def _promoted_fork_runs(graph) -> set:
    """fork_run_ids whose promote provably COMPLETED — a `promote.applied`
    marker for that run exists in the event log. A `loading` promotion whose
    fork run is in this set is a completed promote whose recorder never ran;
    boot can heal it to active WITHOUT guessing (design §10.3)."""
    runs: set = set()
    try:
        for ev in graph.events:
            if getattr(ev, "type", "") == "promote.applied":
                fr = (getattr(ev, "payload", None) or {}).get("from_run", "")
                if fr:
                    runs.add(str(fr))
    except Exception:
        pass
    return runs


def _boot_gap(graph, description: str, meta: dict) -> None:
    """Every boot heal/park raises a reflection capability_gap — the
    hash-mismatch-at-boot precedent: nothing boot does to reconcile an
    ambiguous state is ever silent."""
    graph.add_object("capability_gap", {
        "kind": "reflection", "description": description,
        "status": "open", "metadata": {"source": "boot_heal", **meta}})


def reload_adopted_packs(rt) -> dict[str, str]:
    """Returns {pack_name: outcome}, acting exactly ONCE per pack name.

    Grouped, healed, then resolved by recency. Boot heals ONLY what the
    event log makes unambiguous, and every heal raises a reflection
    capability_gap (design §10.3, the hash-mismatch-at-boot precedent):

    - a `loading` record whose `promote.applied` marker is in the log is a
      provably-completed promote whose recorder never ran → resolve to
      active, load, gap (heals §10.1 case 6, the serious window: live
      promoted state with the defining pack permanently unloaded);
    - a `loading` record with NO marker is an incomplete adoption the log
      does not decide → park it (terminal, + gap), load nothing; the chassis
      re-runs its open ticket against a clean slate (heals §10.1 case 5);
    - two active promotions for one pack name → supersede the older by
      recency (consistent with adoption-time supersession), load the
      survivor, gap;
    - otherwise recency decides: an active shadowed by a MORE RECENT disable
      stays down. Heal never guesses — anything the log leaves ambiguous
      beyond the two cases above stays parked."""
    graph = rt.graph
    promoted_runs = _promoted_fork_runs(graph)

    groups: dict[str, list] = {}
    for promotion in list(graph.objects(type="mod_promotion")):
        name = (promotion.data or {}).get("pack_name", "?")
        groups.setdefault(name, []).append(promotion)

    outcomes: dict[str, str] = {}
    for name, group in groups.items():
        # --- Heal or park loading records first (never guesses) ---
        for p in group:
            data = p.data or {}
            if data.get("status") != "loading":
                continue
            fork = str(data.get("fork_run_id", ""))
            proposal_id = data.get("proposal_id", "")
            if fork and fork in promoted_runs:
                # Case 6 heal: the promote completed; only the recorder was
                # lost. Resolve to active and close any still-open adoption
                # ticket so the chassis does not re-run it and wedge.
                graph.patch_object(p.id, {
                    "status": "active",
                    "status_note": "healed at boot: promote.applied present "
                                   "in the log"})
                for t in graph.objects(type="adoption_ticket"):
                    if ((t.data or {}).get("proposal_id") == proposal_id
                            and (t.data or {}).get("status") == "open"):
                        graph.patch_object(t.id, {
                            "status": "done",
                            "status_note": "closed by boot heal: promote "
                                           "already applied"})
                print(f"[evolution] BOOT HEAL: {name!r} loading promotion "
                      f"{p.id} had a completed promote; resolved to active",
                      flush=True)
                _boot_gap(graph,
                          f"adopted pack {name!r}: a loading promotion "
                          f"({p.id}) had a completed promote (promote.applied "
                          "in the log) but the recorder never ran; boot "
                          "resolved it to active and loaded the pack.",
                          {"promotion_id": str(p.id), "pack_name": name,
                           "case": "loading_with_marker"})
            else:
                # Case 5 park: incomplete adoption, the log does not decide.
                graph.patch_object(p.id, {
                    "status": "disabled",
                    "status_note": "parked at boot: incomplete adoption, no "
                                   "promote.applied in the log"})
                print(f"[evolution] BOOT PARK: {name!r} loading promotion "
                      f"{p.id} is an incomplete adoption; parked", flush=True)
                _boot_gap(graph,
                          f"adopted pack {name!r}: a loading promotion "
                          f"({p.id}) had no promote.applied in the log (an "
                          "incomplete adoption); boot parked it. The chassis "
                          "re-runs its open ticket against a clean slate.",
                          {"promotion_id": str(p.id), "pack_name": name,
                           "case": "loading_no_marker"})

        actives = [p for p in group
                   if (graph.get_object(p.id).data or {}).get("status")
                   == "active"]

        # --- Two-or-more actives: supersede older by recency, load survivor ---
        if len(actives) >= 2:
            survivor = actives[-1]
            losers = actives[:-1]
            for lo in losers:
                meta = dict((lo.data or {}).get("metadata") or {})
                meta["superseded_by"] = str(survivor.id)
                graph.patch_object(lo.id, {
                    "status": "disabled",
                    "status_note": f"superseded by {survivor.id} (boot heal)",
                    "metadata": meta})
            ids = [str(p.id) for p in actives]
            print(f"[evolution] BOOT HEAL: {name!r} had {len(actives)} active "
                  f"promotions {ids}; superseded "
                  f"{[str(l.id) for l in losers]} by {survivor.id}", flush=True)
            _boot_gap(graph,
                      f"adopted pack {name!r} had {len(actives)} active "
                      f"promotions at boot ({ids}); boot superseded the older "
                      f"by recency and loaded {survivor.id}.",
                      {"promotion_ids": ids, "loaded": str(survivor.id),
                       "pack_name": name, "case": "two_active"})
            outcomes[name] = _reload_one(
                rt, graph, survivor, name,
                suffix=" (healed: superseded older active)")
            continue

        # --- Recency resolution (single active or none) ---
        if len(actives) == 1 and str(actives[0].id) == str(group[-1].id):
            outcomes[name] = _reload_one(rt, graph, actives[0], name)
        elif len(actives) == 1:
            # A single active shadowed by a MORE RECENT disable: recency says
            # the pack was disabled after this active. Stays down.
            outcomes[name] = "disabled (stays down, superseded by recency)"
        else:
            status = (graph.get_object(group[-1].id).data or {}).get("status")
            if status == "disabled":
                outcomes[name] = "disabled (stays down)"
            else:
                outcomes[name] = f"skipped (status={status})"
    return outcomes
