"""Agent Profile Pack fixtures — v0.1.

Fixture: profile_context_assembly
  An AgentProfile, Goals, StandingInstructions, PersonalityProfiles,
  and OwnerPreferences are registered. Two ProfileContextRequests are
  made — one owner-facing and one external-facing. Verify that context
  is correctly filtered (mission hidden from external, scoped instructions).

Run:
    python packs/agent_profile/fixtures/run_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

from activegraph import Graph, Runtime
from packs.agent_profile import pack as ap_pack, AgentProfileSettings
from packs.agent_profile.behaviors import clear_profile_registry
from packs.agent_profile.tools import register_profile_fn, request_profile_context_fn


def run_profile_context_assembly() -> bool:
    print("\n" + "=" * 60)
    print("Fixture: profile_context_assembly")
    print("  Owner vs. external context filtering; channel-scoped instructions.")
    print("=" * 60)

    clear_profile_registry()

    graph = Graph()
    rt = Runtime(graph)
    rt.load_pack(ap_pack, settings=AgentProfileSettings(
        owner_name="Alice",
        expose_mission_to_external=False,
        default_tone="direct",
        max_standing_instructions=10,
    ))

    # --- Register the AgentProfile ---
    profile = register_profile_fn(
        graph,
        name="Aria",
        mission="Help Alice build Northwind Robotics into a category-defining company.",
        personality_description="Direct, analytical, and candid. Respects Alice's time.",
        owner_name="Alice",
    )
    rt.run_until_idle()

    # --- Add Goals ---
    goal_fr = graph.add_object("goal", {
        "text": "Close a $3M seed round by Q3 2026.",
        "priority": "critical",
        "status": "active",
        "domain": "fundraising",
        "profile_id": profile.id,
        "metadata": {},
    })
    goal_prod = graph.add_object("goal", {
        "text": "Ship v1.0 of the core product by August.",
        "priority": "high",
        "status": "active",
        "domain": "product",
        "profile_id": profile.id,
        "metadata": {},
    })
    goal_paused = graph.add_object("goal", {
        "text": "Expand to European market.",
        "priority": "medium",
        "status": "paused",
        "domain": "growth",
        "profile_id": profile.id,
        "metadata": {},
    })
    rt.run_until_idle()

    # --- Add StandingInstructions ---
    instr_global = graph.add_object("standing_instruction", {
        "text": "Always reply in the language the user is writing in.",
        "scope": "global",
        "applies_to_channel": None,
        "applies_to_audience_role": None,
        "priority": 90,
        "active": True,
        "profile_id": profile.id,
        "metadata": {},
    })
    instr_email_ext = graph.add_object("standing_instruction", {
        "text": "When writing to external parties, be concise and professional.",
        "scope": "communication",
        "applies_to_channel": "email",
        "applies_to_audience_role": "external",
        "priority": 80,
        "active": True,
        "profile_id": profile.id,
        "metadata": {},
    })
    instr_owner_chat = graph.add_object("standing_instruction", {
        "text": "For Alice: skip pleasantries, lead with the key point.",
        "scope": "communication",
        "applies_to_channel": "chat",
        "applies_to_audience_role": "owner",
        "priority": 85,
        "active": True,
        "profile_id": profile.id,
        "metadata": {},
    })
    rt.run_until_idle()

    # --- Add PersonalityProfile for external email ---
    graph.add_object("personality_profile", {
        "tone": "formal",
        "verbosity": "concise",
        "formality": "formal",
        "applies_to_channel": "email",
        "applies_to_audience_role": "external",
        "profile_id": profile.id,
        "metadata": {},
    })
    rt.run_until_idle()

    # --- Add OwnerPreferences ---
    graph.add_object("owner_preference", {
        "key": "email_sign_off",
        "value": "Best, Alice",
        "domain": "email",
        "channel": "email",
        "profile_id": profile.id,
        "metadata": {},
    })
    graph.add_object("owner_preference", {
        "key": "preferred_language",
        "value": "English",
        "domain": None,
        "channel": None,
        "profile_id": profile.id,
        "metadata": {},
    })
    rt.run_until_idle()

    # --- Request 1: Owner-facing chat context ---
    print("\n  [Owner-facing chat context]")
    req_owner = request_profile_context_fn(
        graph,
        profile_id=profile.id,
        channel="chat",
        audience_role="owner",
        frame_id="frame_owner_001",
    )
    rt.run_until_idle()

    views = list(graph.objects(type="profile_context_view"))
    owner_views = [v for v in views if v.data.get("audience_role") == "owner"]

    print(f"  profile_context_views (owner): {len(owner_views)}")
    if owner_views:
        v = owner_views[0]
        print(f"    agent_name={v.data.get('agent_name')}")
        print(f"    mission present: {bool(v.data.get('mission'))}")
        print(f"    active_goals count: {len(v.data.get('active_goals', []))}")
        print(f"    standing_instructions: {v.data.get('standing_instructions', [])}")
        print(f"    personality: {v.data.get('personality', {})}")
        print(f"    owner_preferences: {v.data.get('owner_preferences', {})}")

    # --- Request 2: External-facing email context ---
    print("\n  [External-facing email context]")
    req_ext = request_profile_context_fn(
        graph,
        profile_id=profile.id,
        channel="email",
        audience_role="external",
        frame_id="frame_ext_001",
    )
    rt.run_until_idle()

    ext_views = [v for v in list(graph.objects(type="profile_context_view"))
                 if v.data.get("audience_role") == "external"]
    print(f"  profile_context_views (external): {len(ext_views)}")
    if ext_views:
        v = ext_views[0]
        print(f"    mission present (should be False): {bool(v.data.get('mission'))}")
        print(f"    standing_instructions: {v.data.get('standing_instructions', [])}")
        print(f"    personality.tone: {v.data.get('personality', {}).get('tone')}")

    # --- Assertions ---
    failures = []

    if not owner_views:
        failures.append("No owner-facing ProfileContextView created")
    else:
        ov = owner_views[0]
        if ov.data.get("agent_name") != "Aria":
            failures.append(f"Expected agent_name='Aria', got '{ov.data.get('agent_name')}'")
        if not ov.data.get("mission"):
            failures.append("Owner-facing view is missing mission")
        active_goals = ov.data.get("active_goals", [])
        if len(active_goals) < 2:
            failures.append(f"Expected ≥2 active goals, got {len(active_goals)} (paused goal excluded)")
        # Owner chat view should have the owner+chat instruction
        instrs = ov.data.get("standing_instructions", [])
        if not any("Alice" in i or "pleasantries" in i for i in instrs):
            failures.append("Owner chat view missing owner-specific instruction")
        # Global instruction should always be present
        if not any("language" in i.lower() for i in instrs):
            failures.append("Owner view missing global standing instruction")

    if not ext_views:
        failures.append("No external-facing ProfileContextView created")
    else:
        ev = ext_views[0]
        if ev.data.get("mission"):
            failures.append("External view should NOT include mission (expose_mission_to_external=False)")
        instrs = ev.data.get("standing_instructions", [])
        if not any("external" in i.lower() or "concise" in i.lower() for i in instrs):
            failures.append("External email view missing external-scoped instruction")
        if ev.data.get("personality", {}).get("tone") != "formal":
            failures.append(f"External email personality should be formal, got {ev.data.get('personality')}")

    # Check fulfilled_by_profile relations
    all_rels = list(graph.relations())
    rel_types = {r.type for r in all_rels}
    if "fulfilled_by_profile" not in rel_types:
        failures.append("Missing relation: fulfilled_by_profile")

    if failures:
        print("  FAIL:")
        for f in failures:
            print(f"    {f}")
        return False

    print("  PASS")
    return True


def run_all() -> None:
    results = [run_profile_context_assembly()]
    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{total} fixtures passed")
    print("=" * 60 + "\n")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    run_all()
