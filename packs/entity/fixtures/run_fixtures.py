"""Entity Pack fixtures — v0.1.

Fixture: entity_lifecycle
  A source containing person name + email is processed. entity_extractor
  creates mentions. entity_resolver creates Entity objects (or links to
  existing ones). A second source with the same person triggers
  merge_candidate_detector. A merge decision is made.

Run:
    python packs/entity/fixtures/run_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

from activegraph import Graph, Runtime
from packs.core import pack as core_pack, CoreSettings
from packs.entity import pack as entity_pack, EntitySettings
from packs.entity.behaviors import clear_entity_registry
from packs.entity.tools import register_entity_fn, decide_merge_fn


def run_entity_lifecycle() -> bool:
    print("\n" + "=" * 60)
    print("Fixture: entity_lifecycle")
    print("  source → extraction → resolution → merge detection → merge decision")
    print("=" * 60)

    clear_entity_registry()

    graph = Graph()
    rt = Runtime(graph)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(entity_pack, settings=EntitySettings(
        extraction_min_confidence=0.6,
        resolution_similarity_threshold=0.75,
        merge_candidate_threshold=0.85,
        auto_accept_exact_identifier_match=True,
    ))

    # --- Source 1: email mentioning Alice Chen ---
    source1 = graph.add_object("source", {
        "kind": "email",
        "content": (
            "Hi, I'm Alice Chen from Northwind Robotics. "
            "Please reach me at alice.chen@northwind.ai for follow-up. "
            "Our company Northwind Robotics builds autonomous inspection systems."
        ),
        "sender_ref": "alice.chen@northwind.ai",
        "channel": "email",
        "frame_id": "frame_001",
        "metadata": {},
    })
    rt.run_until_idle()

    mentions = list(graph.objects(type="entity_mention"))
    entities = list(graph.objects(type="entity"))

    print(f"\n  After source 1:")
    print(f"  entity_mentions: {len(mentions)}")
    for m in mentions[:5]:
        print(f"    [{m.data.get('entity_type_hint')}] '{m.data.get('text')}' "
              f"confidence={m.data.get('confidence'):.2f} "
              f"resolved={'yes' if m.data.get('entity_id') else 'no'}")
    print(f"  entities created: {len(entities)}")
    for e in entities[:5]:
        print(f"    [{e.data.get('entity_type')}] {e.data.get('name')} "
              f"ids={e.data.get('identifiers', {})}")

    # --- Pre-register an entity that will trigger merge detection ---
    # Register "Northwind Robotics" manually (same name as what extractor found)
    existing_org = register_entity_fn(
        graph,
        name="Northwind Robotics",
        entity_type="organization",
        aliases=["northwind", "northwind robotics inc"],
        identifiers={"domain": "northwind.ai"},
        description="Autonomous inspection robotics company",
    )
    rt.run_until_idle()

    merge_candidates = list(graph.objects(type="merge_candidate"))
    print(f"\n  After registering 'Northwind Robotics' entity:")
    print(f"  merge_candidates: {len(merge_candidates)}")
    for mc in merge_candidates:
        print(f"    score={mc.data.get('similarity_score'):.2f} "
              f"status={mc.data.get('status')} "
              f"reasons={mc.data.get('similarity_reasons')}")

    # --- Source 2: different reference to same person (should resolve to existing entity) ---
    source2 = graph.add_object("source", {
        "kind": "chat_message",
        "content": "Alice Chen asked about pricing. Reply to alice.chen@northwind.ai",
        "channel": "chat",
        "frame_id": "frame_002",
        "metadata": {},
    })
    rt.run_until_idle()

    all_entities = list(graph.objects(type="entity"))
    all_mentions = list(graph.objects(type="entity_mention"))
    resolved_mentions = [m for m in all_mentions if m.data.get("entity_id")]

    print(f"\n  After source 2:")
    print(f"  total entity_mentions: {len(all_mentions)}")
    print(f"  resolved_mentions: {len(resolved_mentions)}")
    print(f"  total entities: {len(all_entities)}")

    # The alice.chen@northwind.ai email mention in source 2 should resolve to
    # the same entity created in source 1 (exact identifier match)
    alice_entities = [e for e in all_entities
                      if "alice" in e.data.get("name", "").lower()
                      or "alice" in str(e.data.get("identifiers", {})).lower()]
    print(f"  alice entities: {len(alice_entities)} (ideally 1 — exact email match dedup)")

    # --- Make a merge decision on a merge_candidate ---
    merge_decisions = []
    if merge_candidates:
        mc = merge_candidates[0]
        decision = decide_merge_fn(
            graph,
            merge_candidate_id=mc.id,
            decision="accepted",
            surviving_entity_id=existing_org.id,
            rationale="Same organization confirmed by domain match.",
            decided_by="fixture_operator",
        )
        merge_decisions.append(decision)
        rt.run_until_idle()

        mc_updated = graph.get_object(mc.id)
        print(f"\n  After merge decision:")
        print(f"  merge_candidate status={mc_updated.data.get('status') if mc_updated else 'N/A'} "
              f"(should be 'accepted')")

    # --- Check relations ---
    all_rels = list(graph.relations())
    rel_types = {r.type for r in all_rels}
    print(f"\n  relation types seen: {sorted(rel_types)}")

    # --- Assertions ---
    failures = []

    if not mentions:
        failures.append("entity_extractor created no EntityMentions from source 1")

    if not entities:
        failures.append("entity_resolver created no Entity objects")

    # Check that the email mention was resolved
    email_mentions = [m for m in all_mentions
                      if "@" in m.data.get("text", "") and m.data.get("entity_id")]
    if not email_mentions:
        failures.append("Email mention not resolved to an entity (auto_accept_exact_identifier_match failed)")

    if "mentions" not in rel_types:
        failures.append("Missing relation: mentions (source → entity_mention)")
    if "refers_to" not in rel_types:
        failures.append("Missing relation: refers_to (entity_mention → entity)")

    # If we registered Northwind Robotics and the extractor found it too,
    # a merge_candidate should exist
    northwind_entities = [e for e in all_entities
                          if "northwind" in e.data.get("name", "").lower()]
    if len(northwind_entities) >= 2 and not merge_candidates:
        failures.append("Expected merge_candidate between duplicate Northwind Robotics entities")

    if merge_candidates:
        mc_updated = graph.get_object(merge_candidates[0].id)
        if mc_updated and mc_updated.data.get("status") != "accepted":
            failures.append(f"Merge candidate status should be 'accepted', got {mc_updated.data.get('status')}")

    if failures:
        print("  FAIL:")
        for f in failures:
            print(f"    {f}")
        return False

    print("  PASS")
    return True


def run_all() -> None:
    results = [run_entity_lifecycle()]
    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{total} fixtures passed")
    print("=" * 60 + "\n")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    run_all()
