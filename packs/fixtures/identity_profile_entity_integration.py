"""Cross-pack integration: Identity/Auth + Agent Profile + Entity — v0.1.

Scenario: An email arrives from an owner contact. The system simultaneously:
  1. Resolves the sender to a Principal (Identity Pack)
  2. Extracts entity mentions from the email body (Entity Pack)
  3. Assembles a behavior-scoped context view for the response (Agent Profile Pack)

The result: the response behavior knows WHO is asking (principal role),
WHAT entities are involved (extracted entities), and HOW to respond (profile context).

Full graph-driven: all steps triggered by behaviors.

Run:
    python packs/fixtures/identity_profile_entity_integration.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from activegraph import Graph, Runtime
from packs.core import pack as core_pack, CoreSettings
from packs.identity_auth import pack as identity_pack, IdentitySettings
from packs.agent_profile import pack as ap_pack, AgentProfileSettings
from packs.entity import pack as entity_pack, EntitySettings
from packs.agent_profile.behaviors import clear_profile_registry
from packs.entity.behaviors import clear_entity_registry
from packs.agent_profile.tools import register_profile_fn, request_profile_context_fn


def run_identity_profile_entity_composition() -> bool:
    print("\n" + "=" * 60)
    print("Cross-Pack Integration: Identity + Agent Profile + Entity")
    print("  Owner email → principal resolved, entities extracted,")
    print("  profile context assembled for scoped response generation.")
    print("=" * 60)

    clear_profile_registry()
    clear_entity_registry()

    graph = Graph()
    rt = Runtime(graph)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(identity_pack, settings=IdentitySettings(
        owner_identifiers=["alice@example.com"],
        default_external_role="external",
        owner_auth_confidence=0.95,
        create_auth_context=True,
        check_permissions_on_proposed_actions=True,
    ))
    rt.load_pack(ap_pack, settings=AgentProfileSettings(
        owner_name="Alice",
        expose_mission_to_external=False,
        default_tone="direct",
    ))
    rt.load_pack(entity_pack, settings=EntitySettings(
        extraction_min_confidence=0.6,
        resolution_similarity_threshold=0.75,
        auto_accept_exact_identifier_match=True,
    ))

    # --- Step 1: Register the agent profile (before the email arrives) ---
    print("\n[1] Registering agent profile...")
    profile = register_profile_fn(
        graph,
        name="Aria",
        mission="Help Alice build Northwind Robotics into a category-defining company.",
        personality_description="Direct, analytical, candid. Respects Alice's time.",
        owner_name="Alice",
    )
    graph.add_object("standing_instruction", {
        "text": "For Alice: lead with the answer, then the reasoning.",
        "scope": "communication",
        "applies_to_channel": "email",
        "applies_to_audience_role": "owner",
        "priority": 90,
        "active": True,
        "profile_id": profile.id,
        "metadata": {},
    })
    graph.add_object("goal", {
        "text": "Close a $3M seed round by Q3 2026.",
        "priority": "critical",
        "status": "active",
        "domain": "fundraising",
        "profile_id": profile.id,
        "metadata": {},
    })
    rt.run_until_idle()
    print(f"  profile '{profile.data.get('name')}' registered")

    # --- Step 2: Incoming email from owner about a VC firm + person ---
    print("\n[2] Incoming owner email with entity mentions...")
    email_source = graph.add_object("source", {
        "kind": "email",
        "content": (
            "Hi, just got off a call with Sarah Thompson from Sequoia Capital. "
            "She's interested in Northwind Robotics and wants a deck. "
            "Her email is sarah.thompson@sequoia.com. "
            "Let's follow up on the seed round — "
            "can you draft a first email to her?"
        ),
        "sender_ref": "alice@example.com",
        "channel": "email",
        "frame_id": "frame_incoming_001",
        "metadata": {},
    })
    rt.run_until_idle()

    # --- Inspect what was created ---
    principals = list(graph.objects(type="principal"))
    auth_contexts = list(graph.objects(type="auth_context"))
    entity_mentions = list(graph.objects(type="entity_mention"))
    entities = list(graph.objects(type="entity"))

    owner_principals = [p for p in principals if p.data.get("role") == "owner"]

    print(f"\n  principals: {len(principals)} (owner: {len(owner_principals)})")
    print(f"  auth_contexts: {len(auth_contexts)}")
    print(f"  entity_mentions: {len(entity_mentions)}")
    for m in entity_mentions[:8]:
        print(f"    [{m.data.get('entity_type_hint')}] '{m.data.get('text')}' "
              f"resolved={'yes' if m.data.get('entity_id') else 'no'}")
    print(f"  entities: {len(entities)}")
    for e in entities[:6]:
        print(f"    [{e.data.get('entity_type')}] {e.data.get('name')} "
              f"ids={e.data.get('identifiers', {})}")

    # --- Step 3: Assemble owner-facing email context ---
    print("\n[3] Assembling owner-facing email context...")
    req = request_profile_context_fn(
        graph,
        profile_id=profile.id,
        channel="email",
        audience_role="owner",
        frame_id="frame_incoming_001",
    )
    rt.run_until_idle()

    context_views = [v for v in graph.objects(type="profile_context_view")
                     if v.data.get("audience_role") == "owner"]

    if context_views:
        cv = context_views[0]
        print(f"  context view created: agent_name={cv.data.get('agent_name')}")
        print(f"  mission: {cv.data.get('mission', '')[:60]}...")
        print(f"  active_goals: {len(cv.data.get('active_goals', []))}")
        print(f"  standing_instructions: {cv.data.get('standing_instructions', [])}")
        print(f"  personality.tone: {cv.data.get('personality', {}).get('tone')}")

    # --- Step 4: Verify the full integration shape ---
    print("\n[4] Verifying integration shape...")

    all_relations = list(graph.relations())
    rel_types = {r.type for r in all_relations}
    print(f"  relation types: {sorted(rel_types)}")

    # Show which entities came from the email
    sequoia_entities = [e for e in entities if "sequoia" in e.data.get("name", "").lower()]
    northwind_entities = [e for e in entities if "northwind" in e.data.get("name", "").lower()]
    sarah_entities = [e for e in entities
                      if "sarah" in e.data.get("name", "").lower()
                      or "thompson" in e.data.get("name", "").lower()
                      or "sequoia.com" in str(e.data.get("identifiers", {}))]

    print(f"  Northwind entities: {len(northwind_entities)}")
    print(f"  Sequoia Capital entities: {len(sequoia_entities)}")
    print(f"  Sarah Thompson entities: {len(sarah_entities)}")

    # The "full picture" a response behavior can now access:
    # - principal.role = "owner" → trusted context
    # - auth_context.principal_role = "owner"
    # - entity mentions: Northwind Robotics, Sequoia Capital, Sarah Thompson, email
    # - profile_context_view: mission, goals (seed round), instructions (lead with answer)

    # --- Assertions ---
    failures = []

    if not owner_principals:
        failures.append("principal_resolver: no owner principal created")

    if not auth_contexts:
        failures.append("auth_context_builder: no auth_context created")

    if not entity_mentions:
        failures.append("entity_extractor: no entity_mentions extracted from email")

    if not entities:
        failures.append("entity_resolver: no entities created")

    if not context_views:
        failures.append("profile_context_provider: no context view assembled")
    else:
        cv = context_views[0]
        if not cv.data.get("mission"):
            failures.append("Owner-facing context missing mission")
        if len(cv.data.get("active_goals", [])) < 1:
            failures.append("Context view missing active goals")

    if "resolves_to" not in rel_types:
        failures.append("Missing relation: resolves_to")
    if "authenticated_by" not in rel_types:
        failures.append("Missing relation: authenticated_by")
    if "refers_to" not in rel_types:
        failures.append("Missing relation: refers_to")
    if "fulfilled_by_profile" not in rel_types:
        failures.append("Missing relation: fulfilled_by_profile")

    if failures:
        print("  FAIL:")
        for f in failures:
            print(f"    {f}")
        return False

    print("\n  PASS")
    print("  A response behavior can now see:")
    print(f"    WHO asked: owner principal (confidence={owner_principals[0].data.get('auth_confidence')})")
    print(f"    WHAT entities: {[e.data.get('name') for e in entities[:4]]}")
    if context_views:
        print(f"    HOW to respond: {context_views[0].data.get('standing_instructions', [])[:2]}")
    return True


def run_all() -> None:
    results = [run_identity_profile_entity_composition()]
    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{total} integration fixtures passed")
    print("=" * 60 + "\n")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    run_all()
