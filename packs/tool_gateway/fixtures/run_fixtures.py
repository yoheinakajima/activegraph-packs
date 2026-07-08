"""Run Tool Gateway Pack fixture scenarios.

The full behavior chain is now graph-driven:
  capability_call.created → call_recorder + policy_enforcer
  capability_approval.created → call_executor → creates capability_result
  capability_result.created → result_sourcer → creates source

Usage:
    python packs/tool_gateway/fixtures/run_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parents[2]))

import yaml
from activegraph import Graph, Runtime
from packs.core import pack as core_pack, CoreSettings
from packs.tool_gateway import pack as tg_pack, ToolGatewaySettings
from packs.tool_gateway.tools import (
    approve_capability_fn,
    deny_capability_fn,
    pending_approvals_fn,
    register_local_capability,
)


def _run_fixture(name: str, scenario: dict) -> tuple[bool, list[str]]:
    failures: list[str] = []

    graph = Graph()
    rt = Runtime(graph)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(tg_pack, settings=ToolGatewaySettings(
        auto_approve_risk_classes=["low", "medium"],
    ))

    # Register a mock local capability
    register_local_capability("CRM API", "lookup_company", lambda company_name="": {
        "company": company_name, "founded": 2021, "arr": "$2.4M"
    })

    # Create declared objects
    created_ids: dict[str, list[str]] = {}
    for obj_spec in scenario.get("objects", []):
        obj_type = obj_spec["type"]
        obj_data = dict(obj_spec["data"])

        # Resolve PLACEHOLDER_PROVIDER_ID
        if "provider_id" in obj_data and obj_data["provider_id"] == "PLACEHOLDER_PROVIDER_ID":
            if created_ids.get("capability_provider"):
                obj_data["provider_id"] = created_ids["capability_provider"][0]

        obj = graph.add_object(obj_type, obj_data)
        created_ids.setdefault(obj_type, []).append(obj.id)

    rt.run_until_idle()

    # --- Optional manual decision step (approve/deny a held call) ---
    # Exercises the full held-call lifecycle: policy_enforcer holds the call
    # at 'policy_checking', pending_approvals lists it, an approver resolves
    # it, and run_until_idle lets call_executor react to the approval.
    decision = scenario.get("manual_decision")
    if decision:
        pending = pending_approvals_fn(graph)
        if not pending:
            failures.append("  manual_decision: expected a held call, but pending_approvals is empty")
        else:
            call_id = pending[0]["call_id"]
            if decision.get("action") == "approve":
                outcome = approve_capability_fn(
                    graph, call_id,
                    approver_ref=decision.get("approver_ref", "user:owner"),
                    note=decision.get("note", ""),
                )
            else:
                outcome = deny_capability_fn(
                    graph, call_id,
                    approver_ref=decision.get("approver_ref", "user:owner"),
                    reason=decision.get("reason", ""),
                )
            if not outcome.get("ok"):
                failures.append(f"  manual_decision: {decision['action']} failed — {outcome.get('reason')}")
            rt.run_until_idle()
        if pending_approvals_fn(graph):
            failures.append("  manual_decision: call still pending after decision")

    # Gather state
    all_relations = list(graph.relations())
    relation_types = {r.type for r in all_relations}
    by_type: dict[str, list] = {}
    for o in graph.objects():
        by_type.setdefault(o.type, []).append(o)

    expected = scenario.get("expected_outputs", {})

    # --- Check relations ---
    if "relations" in expected:
        for rel_spec in expected["relations"].get("includes", []):
            rtype = rel_spec["type"]
            if rtype not in relation_types:
                failures.append(
                    f"  relations: expected '{rtype}' ({rel_spec.get('description','')}), "
                    f"not found. Present: {sorted(relation_types)}"
                )

    # --- Print full call lifecycle state ---
    calls = by_type.get("capability_call", [])
    approvals = by_type.get("capability_approval", [])
    denials = by_type.get("capability_denial", [])
    results = by_type.get("capability_result", [])
    sources = by_type.get("source", [])

    print(f"  capability_call: {len(calls)}")
    for c in calls:
        print(f"    status={c.data.get('status', 'n/a')} name={c.data.get('capability_name', '')}")

    print(f"  capability_approval: {len(approvals)} (graph-driven execution trigger)")
    if denials:
        print(f"  capability_denial: {len(denials)} (audited refusals)")
    print(f"  capability_result: {len(results)} (created by call_executor behavior)")
    print(f"  source: {len(sources)} (created by result_sourcer behavior)")

    # --- Check final call statuses ---
    for expected_status in expected.get("call_statuses", []):
        actual = [c.data.get("status") for c in calls]
        if expected_status not in actual:
            failures.append(
                f"  call_statuses: expected a call at {expected_status!r}, got {actual}"
            )

    # Verify the chain ran to the end the scenario expects
    denied = bool(decision) and decision.get("action") == "deny"
    if denied:
        if not denials:
            failures.append("  Denial expected but no capability_denial object was created")
        if results:
            failures.append("  Denied call must not execute, but a capability_result exists")
    else:
        if calls and not approvals:
            failures.append("  No capability_approval objects — policy_enforcer did not fire or approve")
        if approvals and not results:
            failures.append("  capability_approval exists but no capability_result — call_executor did not fire")

    return (len(failures) == 0), failures


def main():
    _HERE = Path(__file__).parent
    fixtures = sorted(_HERE.glob("*.yaml"))

    if not fixtures:
        print("No YAML fixtures found.")
        sys.exit(1)

    results = []
    for fpath in fixtures:
        scenario = yaml.safe_load(fpath.read_text())
        name = fpath.stem
        print(f"\n{'='*60}\nFixture: {name}\n{'='*60}")
        passed, failures = _run_fixture(name, scenario)
        results.append((name, passed))
        if passed:
            print("  PASS")
        else:
            print(f"  FAIL — {len(failures)} failure(s):")
            for f in failures:
                print(f)

    total = len(results)
    passed_count = sum(1 for _, ok in results if ok)
    print(f"\n{'='*60}\nResults: {passed_count}/{total} fixtures passed\n{'='*60}\n")
    if passed_count < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
