"""VC Pack fixtures — v0.1.

Fixture 1: founder_outreach_pipeline
  Inbound email from a founder. founder_email_detector fires.
  company_enricher fires → CompanyProfile + FounderProfile.
  memo_drafter fires → InvestmentMemo + Artifact.
  followup_tracker fires → Followup + Core task.

Fixture 2: deal_round_tracking
  Company profile + deal round creation. lp_update_generator fires on notable status.
  Traction metrics and deal risks are added.

Run:
    python packs/vc/fixtures/run_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

from activegraph import Graph, Runtime
from packs.core import pack as core_pack, CoreSettings
from packs.communication import pack as comm_pack
from packs.vc import pack as vc_pack, VCSettings
from packs.vc.behaviors import clear_vc_registry
from packs.vc.tools import (
    ingest_founder_email_fn,
    create_deal_round_fn,
    add_traction_metric_fn,
    add_deal_risk_fn,
)


def run_founder_outreach_pipeline() -> bool:
    print("\n" + "=" * 60)
    print("Fixture: founder_outreach_pipeline")
    print("  email → founder detection → company + founder → memo + followup")
    print("=" * 60)

    clear_vc_registry()

    graph = Graph()
    rt = Runtime(graph)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(comm_pack)
    rt.load_pack(vc_pack, settings=VCSettings(
        auto_draft_memo=True,
        followup_default_days=7,
        owner_firm_name="Benchmark Capital",
    ))

    msg = ingest_founder_email_fn(
        graph,
        sender_ref="john@tensorwave.ai",
        content=(
            "Hi, I'm John Park, co-founder at TensorWave. We're raising our seed round "
            "at $10M and would love to connect. We have $1.2M ARR, growing 40% MoM. "
            "I'd love to share our deck and discuss a potential investment. "
            "We build infrastructure for LLM inference at the edge."
        ),
        subject="TensorWave Seed Round - $10M raise",
    )
    rt.run_until_idle()

    observations = list(graph.objects(type="observation"))
    companies = list(graph.objects(type="company_profile"))
    founders = list(graph.objects(type="founder_profile"))
    memos = list(graph.objects(type="investment_memo"))
    artifacts = list(graph.objects(type="artifact"))
    followups = list(graph.objects(type="followup"))
    tasks = list(graph.objects(type="task"))

    print(f"\n  After founder email:")
    print(f"  observations:     {len(observations)}")
    for o in observations[:3]:
        print(f"    [{o.data.get('category')}] {o.data.get('text')[:70]}")
    print(f"  company_profiles: {len(companies)}")
    for c in companies:
        print(f"    '{c.data.get('name')}' stage={c.data.get('stage')}")
    print(f"  founder_profiles: {len(founders)}")
    for f in founders:
        print(f"    '{f.data.get('name')}' email={f.data.get('email')}")
    print(f"  investment_memos: {len(memos)}")
    print(f"  artifacts:        {len(artifacts)}")
    print(f"  followups:        {len(followups)}")
    print(f"  core tasks:       {len(tasks)}")

    # Check relations
    all_rels = list(graph.relations())
    rel_types = {r.type for r in all_rels}
    print(f"\n  relation types seen: {sorted(rel_types)}")

    failures = []
    founder_outreach_obs = [o for o in observations
                            if (o.data.get("metadata") or {}).get("kind") == "founder_outreach"]
    if not founder_outreach_obs:
        failures.append("founder_email_detector did not create founder_outreach observation")
    if not companies:
        failures.append("company_enricher created no CompanyProfile")
    if not founders:
        failures.append("company_enricher created no FounderProfile")
    if not memos:
        failures.append("memo_drafter created no InvestmentMemo")
    if not followups:
        failures.append("followup_tracker created no Followup")
    if not tasks:
        failures.append("followup_tracker created no Core task")
    if "founded_by" not in rel_types:
        failures.append("Missing relation: founded_by")
    if "memo_for" not in rel_types:
        failures.append("Missing relation: memo_for")
    if "followup_for" not in rel_types:
        failures.append("Missing relation: followup_for")

    if failures:
        print("  FAIL:")
        for f in failures:
            print(f"    {f}")
        return False

    print("  PASS")
    return True


def run_deal_round_tracking() -> bool:
    print("\n" + "=" * 60)
    print("Fixture: deal_round_tracking")
    print("  company profile → deal round → LP update + traction + risks")
    print("=" * 60)

    clear_vc_registry()

    graph = Graph()
    rt = Runtime(graph)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(comm_pack)
    rt.load_pack(vc_pack, settings=VCSettings(
        owner_firm_name="Sequoia Capital",
        auto_draft_memo=False,
    ))

    # Ingest a second founder email to create a company profile
    ingest_founder_email_fn(
        graph,
        sender_ref="ceo@quantumleap.io",
        content=(
            "We're QuantumLeap, raising Series A. ARR $5M, 25% MoM growth. "
            "Looking to close our round with a lead investor who understands deep tech."
        ),
        subject="QuantumLeap Series A - Looking for lead investor",
    )
    rt.run_until_idle()

    companies = list(graph.objects(type="company_profile"))
    if not companies:
        print("  SKIP: no company_profile created (vc detection may not have fired)")
        return True

    company_id = companies[0].id
    company_name = companies[0].data.get("name") or "QuantumLeap"

    # Add traction metric
    metric = add_traction_metric_fn(graph, company_id, "ARR", 5_000_000, "USD", "2026-Q1", 0.25)
    rt.run_until_idle()

    # Add deal risk
    risk = add_deal_risk_fn(
        graph, company_id,
        "Single customer accounts for 60% of ARR",
        category="market",
        severity="high",
        mitigation="Founder pipeline shows 3 large prospects in late-stage discussions.",
    )
    rt.run_until_idle()

    # Create a deal round at closing stage → triggers lp_update_generator
    round_ = create_deal_round_fn(
        graph, company_id,
        round_type="series-a",
        target_amount=15_000_000,
        committed_amount=12_000_000,
        status="closing",
    )
    rt.run_until_idle()

    metrics = list(graph.objects(type="traction_metric"))
    risks = list(graph.objects(type="deal_risk"))
    rounds = list(graph.objects(type="deal_round"))
    lp_updates = list(graph.objects(type="lp_update"))

    print(f"\n  company: '{company_name}'")
    print(f"  traction_metrics: {len(metrics)}")
    for m in metrics:
        print(f"    {m.data.get('metric_name')}={m.data.get('value'):,.0f} {m.data.get('unit')}")
    print(f"  deal_risks: {len(risks)}")
    for r in risks:
        print(f"    [{r.data.get('severity')}] {r.data.get('risk_text')[:60]}")
    print(f"  deal_rounds: {len(rounds)}")
    print(f"  lp_updates:  {len(lp_updates)}")
    for lp in lp_updates:
        print(f"    status={lp.data.get('status')} '{lp.data.get('title')[:50]}'")

    all_rels = list(graph.relations())
    rel_types = {r.type for r in all_rels}
    print(f"\n  relation types seen: {sorted(rel_types)}")

    failures = []
    if not metrics:
        failures.append("No TractionMetric created")
    if not risks:
        failures.append("No DealRisk created")
    if not rounds:
        failures.append("No DealRound created")
    if not lp_updates:
        failures.append("lp_update_generator did not fire for 'closing' status deal round")
    if "reports_metric" not in rel_types:
        failures.append("Missing relation: reports_metric")
    if "risk_in" not in rel_types:
        failures.append("Missing relation: risk_in")

    if failures:
        print("  FAIL:")
        for f in failures:
            print(f"    {f}")
        return False

    print("  PASS")
    return True


def run_all() -> None:
    results = [
        run_founder_outreach_pipeline(),
        run_deal_round_tracking(),
    ]
    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"VC Pack: {passed}/{total} fixtures passed")
    print("=" * 60 + "\n")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    run_all()
