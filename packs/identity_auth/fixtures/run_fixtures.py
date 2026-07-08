"""Identity/Auth Pack fixtures — v0.1.

Fixture: principal_lifecycle
  A source arrives with a known sender_ref. principal_resolver creates a
  Principal with role='owner' (matching owner_identifiers). auth_context_builder
  creates an AuthContext. A proposed action is checked by permission_checker.

Run:
    python packs/identity_auth/fixtures/run_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

from activegraph import Graph, Runtime
from packs.core import pack as core_pack, CoreSettings
from packs.identity_auth import pack as identity_pack, IdentitySettings


def run_principal_lifecycle() -> bool:
    print("\n" + "=" * 60)
    print("Fixture: principal_lifecycle")
    print("  A source resolves to owner principal, auth_context created,")
    print("  permission_checker validates proposed action.")
    print("=" * 60)

    graph = Graph()
    rt = Runtime(graph)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(identity_pack, settings=IdentitySettings(
        owner_identifiers=["alice@example.com", "alice_slack"],
        default_external_role="external",
        owner_auth_confidence=0.95,
    ))

    # --- Add a source from the owner ---
    source_owner = graph.add_object("source", {
        "kind": "chat_message",
        "content": "Please draft a summary of last week.",
        "sender_ref": "alice@example.com",
        "channel": "chat",
        "frame_id": "frame_001",
        "metadata": {},
    })
    rt.run_until_idle()

    principals = list(graph.objects(type="principal"))
    auth_contexts = list(graph.objects(type="auth_context"))

    owner_principals = [p for p in principals if p.data.get("role") == "owner"]

    print(f"  principals created: {len(principals)}")
    print(f"  owner principals: {len(owner_principals)}")
    if owner_principals:
        p = owner_principals[0]
        print(f"    name={p.data.get('name')} role={p.data.get('role')} "
              f"confidence={p.data.get('auth_confidence')}")
    print(f"  auth_contexts created: {len(auth_contexts)}")
    if auth_contexts:
        ac = auth_contexts[0]
        print(f"    principal_role={ac.data.get('principal_role')} "
              f"channel={ac.data.get('channel')}")

    # --- Add a source from an unknown external ---
    source_ext = graph.add_object("source", {
        "kind": "email",
        "content": "Hi, I'm interested in your product.",
        "sender_ref": "vendor@unknown.org",
        "channel": "email",
        "frame_id": "frame_002",
        "metadata": {},
    })
    rt.run_until_idle()

    all_principals = list(graph.objects(type="principal"))
    external_principals = [p for p in all_principals if p.data.get("role") in ("external", "unknown")]
    print(f"  external principals: {len(external_principals)}")
    if external_principals:
        ep = external_principals[0]
        print(f"    role={ep.data.get('role')} confidence={ep.data.get('auth_confidence')}")

    # --- Propose an action from the owner (should be permission_checked=True) ---
    action_allowed = graph.add_object("action", {
        "kind": "tool_call",
        "description": "Look up CRM data",
        "status": "proposed",
        "risk_class": "low",
        "metadata": {"principal_id": owner_principals[0].id if owner_principals else ""},
    })
    rt.run_until_idle()

    action_obj = graph.get_object(action_allowed.id)
    action_meta = action_obj.data.get("metadata", {}) if action_obj else {}
    print(f"  owner action status={action_obj.data.get('status') if action_obj else 'N/A'} "
          f"permission_checked={action_meta.get('permission_checked')}")

    # --- Propose a high-risk action from an external (should be rejected) ---
    if external_principals:
        action_blocked = graph.add_object("action", {
            "kind": "external_write",
            "description": "Modify production data",
            "status": "proposed",
            "risk_class": "high",
            "metadata": {"principal_id": external_principals[0].id},
        })
        rt.run_until_idle()

        blocked_obj = graph.get_object(action_blocked.id)
        blocked_status = blocked_obj.data.get("status") if blocked_obj else "N/A"
        print(f"  external high-risk action status={blocked_status} (should be 'rejected')")

    # --- Check relations ---
    all_relations = list(graph.relations())
    relation_types_seen = {r.type for r in all_relations}
    print(f"  relations: {sorted(relation_types_seen)}")

    # --- Assertions ---
    failures = []

    if not owner_principals:
        failures.append("No owner principal created from owner sender_ref")

    if not auth_contexts:
        failures.append("No auth_context created by auth_context_builder")

    if owner_principals and auth_contexts[0].data.get("principal_role") != "owner":
        failures.append(f"AuthContext role expected 'owner', got '{auth_contexts[0].data.get('principal_role')}'")

    if action_obj and action_obj.data.get("status") == "rejected":
        failures.append("Owner's low-risk action was incorrectly rejected")

    if external_principals:
        blocked_obj = graph.get_object(action_blocked.id)
        if blocked_obj and blocked_obj.data.get("status") != "rejected":
            failures.append(f"External high-risk action not rejected (status={blocked_obj.data.get('status')})")

    if "resolves_to" not in relation_types_seen:
        failures.append("Missing relation: resolves_to")
    if "authenticated_by" not in relation_types_seen:
        failures.append("Missing relation: authenticated_by")

    if failures:
        print("  FAIL:")
        for f in failures:
            print(f"    {f}")
        return False

    print("  PASS")
    return True


def run_all() -> None:
    results = [run_principal_lifecycle()]
    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{total} fixtures passed")
    print("=" * 60 + "\n")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    run_all()
