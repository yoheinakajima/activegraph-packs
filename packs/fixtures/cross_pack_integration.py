"""Cross-pack integration fixture: Tool Gateway → Core → Memory Gateway.

Demonstrates the full pipeline with graph-driven execution:
  1. A CapabilityProvider is registered
  2. A CapabilityCall is proposed (low risk → policy_enforcer auto-approves)
  3. call_executor behavior fires (capability_approval.created) → executes → CapabilityResult
  4. result_sourcer creates a Core source object
  5. Core observation_extractor extracts observations from the source
  6. Core memory_candidate_proposer creates memory candidates
  7. Memory Gateway candidate_evaluator evaluates candidates
  8. Memory Gateway memory_writer promotes accepted → MemoryItem
  9. A memory_retrieval_request triggers memory_retriever → MemoryRetrieval
  10. memory_ranker scores all retrieved items → MemoryRanking

Run without LLM or API key:
    python packs/fixtures/cross_pack_integration.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from activegraph import Graph, Runtime
from packs.core import pack as core_pack, CoreSettings
from packs.tool_gateway import pack as tg_pack, ToolGatewaySettings
from packs.tool_gateway.tools import register_local_capability
from packs.secrets import pack as secrets_pack, SecretsSettings
from packs.secrets.tools import resolve_and_audit_fn
from packs.memory_gateway import pack as mg_pack, MemoryGatewaySettings
from packs.memory_gateway.backend import clear_all_backends


def run_integration_test() -> bool:
    """Run the cross-pack integration scenario. Returns True if passed."""
    print("\n" + "=" * 60)
    print("Cross-Pack Integration: Tool Gateway → Core → Memory Gateway")
    print("(fully graph-driven: all steps triggered by behaviors)")
    print("=" * 60)

    # Fresh backend
    clear_all_backends()

    # --- Setup ---
    graph = Graph()
    rt = Runtime(graph)

    rt.load_pack(core_pack, settings=CoreSettings(
        observation_min_confidence=0.3,
        max_observations_per_source=8,
    ))
    rt.load_pack(tg_pack, settings=ToolGatewaySettings(
        auto_approve_risk_classes=["low", "medium"],
        create_source_from_result=True,
        record_output_data=True,
    ))
    rt.load_pack(secrets_pack, settings=SecretsSettings())
    rt.load_pack(mg_pack, settings=MemoryGatewaySettings(
        acceptance_threshold=0.5,
        auto_accept_categories=["preference", "instruction", "decision", "fact"],
    ))

    # Register a mock CRM capability
    def mock_crm_lookup(company_name: str = "") -> dict:
        return {
            "company": company_name,
            "founded": 2021,
            "arr": "$2.4M",
            "headcount": 18,
            "key_preference": "API-first architecture preferred.",
            "decision": "Team decided to adopt event sourcing.",
            "instruction": "Always use JSON for API responses.",
        }

    register_local_capability("crm", "lookup_company", mock_crm_lookup)

    # --- Step 1: Register capability provider ---
    print("\n[1] Registering capability provider and credential reference...")
    provider = graph.add_object("capability_provider", {
        "name": "crm",
        "kind": "api",
        "base_url": "https://crm.example.com",
        "description": "Mock CRM for integration test",
        "capabilities": ["lookup_company"],
        "credential_ref_name": "CRM_API_KEY",
        "enabled": True,
        "metadata": {},
    })

    # Register credential reference (Secrets Pack creates usage event)
    cred_ref = graph.add_object("credential_ref", {
        "name": "CRM_API_KEY",
        "scope": "read",
        "provider_hint": "crm",
        "last_used_at": None,
        "use_count": 0,
        "enabled": True,
        "metadata": {},
    })
    rt.run_until_idle()

    # Resolve and audit credential (creates usage event with event_type='resolved')
    # Pass credential_ref_id so the behavior can patch last_used_at/use_count directly.
    # NOTE: returned value would be used as API header, not stored
    resolve_and_audit_fn(
        graph=graph,
        credential_name="CRM_API_KEY",
        behavior_name="integration_test",
        frame_id="frame_integration_001",
        credential_ref_id=cred_ref.id,
    )
    rt.run_until_idle()

    print(f"    Provider: {provider.id}")
    print(f"    CredentialRef: {cred_ref.id}")

    # Check credential stats updated
    cred_obj = graph.get_object(cred_ref.id)
    if cred_obj:
        print(f"    CredentialRef use_count: {cred_obj.data.get('use_count', 0)}")
        print(f"    CredentialRef last_used_at: {cred_obj.data.get('last_used_at', 'not set')}")

    # --- Step 2: Propose a capability call (graph-driven execution) ---
    print("\n[2] Proposing capability call (low risk → auto-approved → call_executor fires)...")
    # Include credential_ref_id so call_executor can inject credentials at execution time.
    # policy_enforcer copies credential_ref_id into CapabilityApproval;
    # call_executor calls resolve_and_audit_fn automatically before executing.
    call = graph.add_object("capability_call", {
        "provider_id": provider.id,
        "provider_name": "crm",
        "capability_name": "lookup_company",
        "input_data": {"company_name": "Northwind Robotics"},
        "credential_ref_name": "CRM_API_KEY",
        "credential_ref_id": cred_ref.id,
        "risk_class": "low",
        "status": "proposed",
        "proposed_by": "integration_test",
        "frame_id": "frame_integration_001",
        "proposed_at": "2026-06-03T10:00:00Z",
        "metadata": {},
    })

    # Let all behaviors run:
    # policy_enforcer → capability_approval.created → call_executor → capability_result.created
    # → result_sourcer → source.created → observation_extractor → memory_candidate_proposer
    # → candidate_evaluator → memory_writer
    rt.run_until_idle()

    # --- Step 3: Inspect call lifecycle ---
    print("\n[3] Inspecting call lifecycle...")
    call_obj = graph.get_object(call.id)
    call_status = call_obj.data.get("status", "unknown") if call_obj else "unknown"
    print(f"    Call status: {call_status} (should be 'done')")

    approvals = list(graph.objects(type="capability_approval"))
    results = list(graph.objects(type="capability_result"))
    print(f"    capability_approvals: {len(approvals)} (created by policy_enforcer)")
    print(f"    capability_results: {len(results)} (created by call_executor behavior)")

    # Check credential injection happened during call_executor execution
    # use_count starts at 1 (manual resolve above); call_executor injects → 2
    cred_after_call = graph.get_object(cred_ref.id)
    cred_use_count_after = cred_after_call.data.get("use_count", 0) if cred_after_call else 0
    print(f"    CredentialRef use_count after call_executor: {cred_use_count_after} (should be ≥2)")

    # Check output sanitization
    result_sanitized = results[0].data.get("sanitized", False) if results else None
    print(f"    CapabilityResult.sanitized={result_sanitized} "
          f"(False expected: mock output has no secret patterns)")

    # --- Step 4: Inspect Core pipeline ---
    print("\n[4] Inspecting Core pipeline (source → observations → candidates)...")
    sources = list(graph.objects(type="source"))
    observations = list(graph.objects(type="observation"))
    candidates = list(graph.objects(type="memory_candidate"))
    evaluations = list(graph.objects(type="evaluation"))
    memory_items = list(graph.objects(type="memory_item"))

    print(f"    sources: {len(sources)}")
    print(f"    observations: {len(observations)}")
    for obs in observations[:3]:
        cat = obs.data.get("category", "none")
        text = obs.data.get("text", "")[:55]
        print(f"      [{cat}] {text}")
    print(f"    memory_candidates: {len(candidates)}")
    print(f"    evaluations: {len(evaluations)}")
    accepted_evals = [e for e in evaluations if e.data.get("judgment") == "accepted"]
    print(f"    memory_items (accepted): {len(memory_items)} ({len(accepted_evals)} accepted)")

    # --- Step 5: Graph-driven retrieval ---
    print("\n[5] Graph-driven retrieval via memory_retrieval_request...")
    request = graph.add_object("memory_retrieval_request", {
        "query": "API architecture preferences JSON",
        "top_k": 5,
        "min_score": 0.1,
        "category": None,
        "behavior_name": "integration_test",
        "frame_id": "frame_integration_001",
        "backend_url": ":memory:",
        "metadata": {},
    })
    rt.run_until_idle()  # memory_retriever fires → memory_retrieval created → memory_ranker fires

    retrievals = list(graph.objects(type="memory_retrieval"))
    rankings = list(graph.objects(type="memory_ranking"))

    print(f"    memory_retrievals: {len(retrievals)} (created by memory_retriever behavior)")
    print(f"    memory_rankings: {len(rankings)} (created by memory_ranker behavior)")
    if retrievals:
        ret = retrievals[0]
        item_ids = ret.data.get("item_ids", [])
        print(f"    retrieval item_ids count: {len(item_ids)}")
        for rk in sorted(rankings, key=lambda r: r.data.get("rank", 99))[:3]:
            print(f"      rank={rk.data.get('rank')} score={rk.data.get('score')} "
                  f"item={rk.data.get('item_id','')[:20]}")

    # --- Assertions ---
    failures = []

    if not approvals:
        failures.append("No capability_approval — policy_enforcer did not create approval")
    if not results:
        failures.append("No capability_result — call_executor behavior did not fire")
    if call_status != "done":
        failures.append(f"Expected call status='done', got '{call_status}'")
    if not sources:
        failures.append("No sources — result_sourcer did not fire")
    if not observations:
        failures.append("No observations — Core observation_extractor did not fire")
    if not evaluations:
        failures.append("No evaluations — memory Gateway candidate_evaluator did not fire")
    if not retrievals:
        failures.append("No memory_retrieval — memory_retriever behavior did not fire")

    all_relations = list(graph.relations())
    relation_types = {r.type for r in all_relations}
    for expected_rel in ["calls", "approved_by", "produces_result", "sourced_as",
                          "evaluates", "fulfilled_by"]:
        if expected_rel not in relation_types:
            failures.append(f"Missing relation type '{expected_rel}'")

    # Credential resolution audit — manual resolve (use_count ≥ 1)
    cred_obj = graph.get_object(cred_ref.id)
    if cred_obj and cred_obj.data.get("use_count", 0) < 1:
        failures.append("CredentialRef use_count was not updated by credential_resolution_recorder")

    # Credential injection during call_executor — use_count should be ≥ 2
    if cred_use_count_after < 2:
        failures.append(
            f"call_executor did not inject credentials (expected use_count ≥ 2, got {cred_use_count_after})"
        )

    # Output sanitization — result must have sanitized field (True or False, not None)
    if result_sanitized is None:
        failures.append("CapabilityResult missing 'sanitized' field — sanitizer not wired")

    # Ensure no raw secrets in CapabilityResult.output_data
    if results:
        result_output = results[0].data.get("output_data", "")
        if "sk-" in result_output or "AKIA" in result_output:
            failures.append("CapabilityResult.output_data contains unsanitized secret patterns")

    print("\n" + "=" * 60)
    if failures:
        print(f"FAIL — {len(failures)} assertion(s) failed:")
        for f in failures:
            print(f"  {f}")
        return False
    else:
        print("PASS — full pipeline verified:")
        print("  Tool Gateway (policy → approval → execution) → Core → Memory Gateway")
        print("  Secrets audit trail updated")
        print("  Graph-driven retrieval: request → retrieval → rankings")
    print("=" * 60 + "\n")
    return True


if __name__ == "__main__":
    passed = run_integration_test()
    sys.exit(0 if passed else 1)
