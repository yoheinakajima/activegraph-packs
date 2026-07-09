# Soak runbook (LLM-author gate 5)

The soak is the one gate only time retires: the full evolution loop,
unattended, for days, on a keyless machine, with every unhappy path
exercised on rotation. This document is how you run it, what healthy
looks like, and when to stop and report.

The harness lives at `packs/evolution/soak.py`. Design of record for
what it exercises: docs/evolution-design.md; the gate it serves:
docs/llm-author-design.md §7.

## What one rotation does

Each rotation boots a FRESH runtime against the persistent soak store
(so boot housekeeping, principal re-indexing, and adopted-pack reload
run every time), then walks seven paths with the scripted author:

| path | exercises | expected terminal state |
|---|---|---|
| tainted | deterministic taint inheritance | proposal `suspended`, zero gates run, sweeps leave it alone |
| happy | the full loop: gap, gates, subprocess trial, held critical call, scripted approval by the registered owner principal, adopt, watch window | pack live on the parent, behavior fires, previous rotation's pack governed away (`disabled`) |
| conflict_park | the retry cap | `needs_owner` after exactly `max_conflict_retries` requeues; further sweeps do nothing |
| budget_events | the runtime's event-budget net | rejected, child outcome `limits_exceeded` at replay |
| budget_wallclock | the parent-side wall-clock kill | rejected, child outcome `limits_exceeded` at the fixture gate |
| budget_memory | runaway-memory containment (platform-conditional, see below) | rejected; contained by whichever memory-related net is real on this box |
| disable_restart | governed disable plus restart persistence | `disabled (stays down)` in the reload report after a real restart |

Every path asserts its terminal state. A miss is recorded as an
ANOMALY with a traceback and the rotation continues; anomalies are
never swallowed.

After the seven paths, the harness also asserts its own **post-rotation
invariant**: at most ONE `active` promotion total (the happy path churns
exactly one at a time). This runs even when every path passed — the
class of bug it guards against once let a rotation score all seven GREEN
while two adopted packs were simultaneously live. A violation is a
first-class anomaly of its own: it names the offending promotions, flips
the digest RED, and lands in the anomaly log, exactly like a path
failure. The external observer's "at most one active" check below is now
belt-and-braces — redundant by design rather than load-bearing.

**budget_memory is platform-conditional, on purpose.** The invariant it
protects is "a runaway-memory candidate is CONTAINED", which is true on
both platforms; the specific net that contains it is not. The soak keys
off the runtime's own memory-net signal (`activegraph.sandbox.preflight`
reports whether `RLIMIT_AS` applies on this box), never `sys.platform`:

- **Linux (memory net live):** the candidate makes a fixed over-cap
  allocation and the memory net catches it at import; expected outcome
  `materialization_failed`. This is the historical Linux behavior,
  unchanged.
- **macOS (memory net OFF):** Darwin cannot set address-space limits, so
  the runtime degrades that net loudly and announces `memory net is
  OFF`. The candidate then allocates UNBOUNDEDLY and the wall-clock kill
  contains it instead; expected outcome `limits_exceeded`. A
  memory-net-OFF warning on macOS is EXPECTED, not a red flag.

The real red flag for this path is a runaway that escapes ALL nets: a
budget_memory candidate whose trial `completed` (verdict pass). That
means nothing contained it, and it is a stop-and-report.

Keyless by construction: the scripted author writes the candidates,
trial children get no LLM provider and an allow-list environment, and
nothing reads an API key. If the machine has keys anyway, the soak
neither needs nor touches them, and the harness is where that claim
is tested daily.

## Environment constraint (read this before you start)

The soak runs the full loop, and the loop runs candidate code in the
runtime's subprocess trial child. That child spawns with a deliberately
closed environment allow-list (a security control: no ambient parent
env, no secrets, leaks into a process running candidate code). As of
activegraph 1.7.0 the parent computes the child's import path from its
own resolved `sys.path`, so the child imports `activegraph` wherever
the parent can (editable, venv, or a Nix/Replit install) while the
allow-list stays closed. Earlier runtimes (1.6.0 and before) stripped
package-discovery too aggressively and the child could not import
`activegraph` on any non-CI install (a stock macOS venv failed the same
way Replit did); this is why the soak pins `activegraph >=1.7`.

You do not have to check the box by hand: the soak runs a **preflight**
before the first rotation, delegating to the runtime's canonical probe
(`activegraph.sandbox.preflight`), which spawns a null-job child under
the real sandbox env. On a capable box it prints `preflight: trial
child OK`. On an incapable one it refuses to run (exit 2) with a message
carrying the child's real error rather than producing a digest full of
identical silent crashes. If you see that refusal, the box cannot host
the soak; the message names the cause.

## Starting it

Fresh machine (standard pip or venv), from the repo root:

```bash
pip install -e ".[dev]"
python -m packs.evolution.soak --root data/soak --interval 600
```

That runs one rotation every 10 minutes, forever, against
`data/soak/soak.sqlite`. Useful variants:

```bash
python -m packs.evolution.soak --root data/soak --once          # one rotation, then exit
python -m packs.evolution.soak --root data/soak --rotations 12  # stop after 12
```

Keep it alive across your own disconnects however you prefer
(`nohup ... &`, tmux, a Replit always-on task). The harness keeps all
its state in `--root`; killing and restarting it is safe — including a
kill in the MIDDLE of a rotation. The happy path persists its adoption
to `state.json` the moment it commits (not only at end-of-rotation), so
a mid-rotation kill that loses the rotation's progress file can no
longer make the re-run disable a stale target and orphan an active
promotion (the Replit rotation-15 mechanism). A killed rotation simply
re-runs from its start on restart.

One rotation takes about half a minute on a small machine (the
wall-clock scenario deliberately burns its 5-second timeout). At the
default 10-minute interval you get roughly 140 rotations per day,
which is far past the per-path target; the interval exists to make the
store's growth and the machine's load boring, and there is no need to
shrink it.

## Reading it

Zero-setup artifact: `data/soak/digests/soak-YYYY-MM-DD.md`, rewritten
after every rotation. Healthy looks like:

- `status: **GREEN**`
- every row of the path table with `anomalies` at 0
- `promotions by status` showing exactly ONE `active` (the current
  rotation's happy pack; everything older reads `disabled`)
- `trials` failing only where the budget paths mean them to (three
  fails per rotation: the three budget candidates)
- the boot line showing `housekeeping` examining fork runs and the
  reload report listing old packs as `disabled (stays down)`

`data/soak/state.json` carries the cumulative counts and the anomaly
log; the console prints one line per rotation. The digest file is
overwritten after every rotation (and named per day), so a transient
RED can be overwritten GREEN on the next rotation — but every anomaly,
invariant violations included, is APPENDED to `state.json`'s
`anomaly_log` and stays there regardless. If you ever see a RED digest,
read `anomaly_log`: it is the durable record, and it does not get
overwritten.

## Red flags (stop and report)

Any of these means stop the harness and bring me the digest plus the
anomaly log; do not restart over them:

- `status: **RED**`, or any nonzero `anomalies` cell. The digest's
  anomaly section now carries both halves: the trial child's own
  failure detail (the real error, when a child failed) and the
  soak-side traceback (which invariant broke). A crash is never opaque
  here anymore; if an anomaly names a child failure, that detail is the
  report, not the soak assertion above it.
- More than one `active` promotion, or an `active` count that grows
  across rotations. The governed-disable path is failing quietly. The
  harness now asserts this itself post-rotation (it surfaces as an
  `invariant` anomaly naming both promotions), so a RED digest gets you
  here without your having to eyeball the count; this bullet stays as
  the belt-and-braces external check.
- A `suspended` count that ever DECREASES, or a parked `needs_owner`
  proposal that changes status. Nothing automatic may touch either.
- A budget path whose candidate `completed` (trial verdict pass): a
  runaway escaped every net. For `budget_memory` specifically, note the
  outcome is platform-conditional (`materialization_failed` on Linux,
  `limits_exceeded` on macOS, per the table above), so the red flag is
  `completed`, not which of those two fired.
- The happy path's `greeting_delta` at 0 with no anomaly recorded: the
  adopted behavior stopped firing without failing.
- A rotation that stops producing digests at all (crash loop, disk
  full, store corruption). The console traceback is the report.
- Any loader manifest WARNING in the console. CI validates every push,
  so a warning here means CI missed something; that is a repo bug, and
  the warning text is the report.

## Stop conditions (when the gate is satisfied)

Both must hold, and the digest computes them for you (`soak target:`
flips to MET):

1. At least **3 days** elapsed since `started_at` in state.json.
2. Every path's `ok` count at least **20**.

The numbers are `DEFAULT_MIN_DAYS` / `DEFAULT_MIN_PER_PATH` in
`packs/evolution/soak.py`; raising them raises the bar, and the digest
states whichever target it was computed against. When the target reads
MET with a GREEN digest and an empty anomaly log, gate 5 is satisfied:
bring me the final digest and the state file, and keep the store (the
whole soak is replayable evidence, which is the point of running it on
an event-sourced substrate).

## Determinism notes

Candidate content is a pure function of the rotation index; timestamps
and scheduling touch the clock; the store accumulates honestly (a
multi-day soak is supposed to grow) while boot housekeeping retires
disposable trial forks each rotation, so growth stays linear in
rotations. Fixture 19 (`packs/evolution/fixtures/run_fixtures.py`)
runs one full rotation in CI on every push, so the harness itself
cannot rot while the soak machine runs an older checkout.
