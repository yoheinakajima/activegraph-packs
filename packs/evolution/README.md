# Evolution Pack — v0.2

Self-modification with provenance. The assistant authors candidate packs;
static gates check them without executing anything; fork trials run them
against replayed history in isolation; a verified owner approves adoption
through the gateway; the runtime's quiescent promote adopts the trial's
state. Every step is graph state. Design of record:
[`docs/evolution-design.md`](../../docs/evolution-design.md).

**Ships disabled.** `EvolutionSettings.enabled` defaults to False, and
`evolution.adopt_proposal` REFUSES to register when the gateway would
auto-approve `critical` or when no verified approver exists.
Unverified-mode self-modification does not exist.

## The loop

```
capability_gap -> mod_proposal -> static gates -> fork trial (+ residue
sweep) -> held approval (critical, via the review page) ->
adoption_ticket -> [chassis, between frames] bundle-hash pin -> gates
re-run -> promote dry-run -> load_pack + mod_promotion(loading) ->
promote (quiescent) -> active -> monitor -> disable (immediate
deregistration + boot exclusion)

conflict -> capped auto-retry (max_conflict_retries) -> needs_owner
```

## The review page

Approving code you have not read defeats the whole threat model, so
`review.py` renders one proposal as one page, from graph state alone:
the author banner first (AUTHORED BY: AGENT, loudly), any injection
flags on the lineage, the gap, the declared surface including
`consumes` (outbound reach), every gate verdict, the trial numbers and
fork run id, and the FULL source diff (small by the size gate, so
actually readable). The demo server serves it at
`/approvals/review?proposal_id=...`; `/approvals` gives browsers the
review index and API clients the same JSON as before.

## Host surface (runs BETWEEN frames, never inside a behavior)

| Function | Job |
|---|---|
| `tools.submit_proposal_fn` | The one authoring entry point (scripted, owner, or LLM author) |
| `trial.run_trial(rt, proposal_id, settings)` | Fixture gate + fork replay (in-sample, then held-out once) |
| `tools.request_adoption_fn` | Propose adoption; critical always holds |
| `adopt.register_adoption_capabilities` | Registers the governed capabilities; refuses unsafe configs |
| `chassis.sweep_evolution(rt, settings)` | The sweep hosts should call: tickets + capped conflict retries |
| `adopt.process_adoption_tickets(rt, settings)` | Phase two: the canonical adopt order (wrapped by the chassis) |
| `review.build_review` / `render_review_html` | The decision surface, from graph state alone |
| `boot.reload_adopted_packs(rt)` | Boot persistence, bundle-hash checked |

Demo server wiring: `ACTIVEGRAPH_EVOLUTION=1` (plus `ACTIVEGRAPH_OWNER`,
required by the registration refusal). Tickets are processed by the
schedule tick driver on the runtime-executor thread.

## The pins

`mod_proposal.bundle_hash` is the runtime's bundle hash over every
submitted byte INCLUDING `manifest.toml` (the document reviewers read).
Recomputed at gate time, at adoption, and at every boot re-load; any
mismatch aborts with nothing loaded.

## Fixtures

```bash
python packs/evolution/fixtures/run_fixtures.py
```

Fifteen acceptance scenarios (design §8): happy path, the six-way static
gate matrix, trial isolation, held-out discipline, conflict-then-retry,
deterministic taint inheritance, self-approval blocked twice,
approve-then-swap dead for source AND manifest-only swaps, restart
persistence with corruption handling, both registration refusals,
loading-state tracking, the apply-time validation ordering proof, the
decision surface rendered end to end, zero trial residue after adoption,
and the conflict retry cap parking at needs_owner.
Scripted author only; no LLM, no keys, no network.
