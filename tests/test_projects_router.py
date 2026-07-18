"""The evidence→workstream router (projects 0.5.0): deterministic signals,
confident-only auto-routing, honest unfiled state, owner corrections as
learning evidence, and owner-authored workstream creation."""

import pytest

from activegraph import Graph, Runtime

from packs.communication import pack as communication_pack
from packs.core import pack as core_pack
from packs.entity import pack as entity_pack
from packs.projects import pack as projects_pack
from packs.projects.graph import (
    associate_workstream_fn,
    correct_routing_fn,
    route_item_fn,
)
from packs.projects.router import (
    AUTO_ROUTE_THRESHOLD_MILLI,
    derive_route_fn,
    route_pending_fn,
    unrouted_items_fn,
)
from packs.projects.tools import (
    archive_project_fn,
    create_workstream_fn,
    describe_project_fn,
)
from packs.subject_profile import pack as subject_profile_pack
from packs.team_ops import pack as team_ops_pack
from packs.team_ops.tools import (
    accept_task_candidate_fn,
    create_task_fn,
    project_tasks_fn,
)


@pytest.fixture()
def runtime():
    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    rt.load_pack(entity_pack)
    rt.load_pack(subject_profile_pack)
    rt.load_pack(communication_pack)
    rt.load_pack(projects_pack)
    rt.load_pack(team_ops_pack)
    rt.run_until_idle()
    return rt


def _thread(graph, *, subject, participants=(), labels=(), suffix="t1"):
    participant_ids = []
    for index, (address, entity_id) in enumerate(participants):
        participant = graph.add_object("conversation_participant", {
            "participant_identity": f"participant_{suffix}_{index}_{address}",
            "thread_id": "pending",
            "address": address,
            "display_name": address.split("@", 1)[0],
            "roles": ["sender"],
            "is_owner": False,
            "entity_id": entity_id,
        })
        participant_ids.append(participant.id)
    return graph.add_object("conversation_thread", {
        "thread_identity": f"thread_{suffix}",
        "source_surface_id": "surface_gmail_test",
        "service": "gmail",
        "account_ref": "acct_test",
        "provider_thread_id": f"prov_{suffix}",
        "subject": subject,
        "participant_ids": participant_ids,
        "labels": list(labels),
        "message_count": 3,
        "unread_count": 1,
    })


def _entity(graph, name, entity_type="organization"):
    return graph.add_object("entity", {
        "name": name, "entity_type": entity_type, "aliases": [],
    })


def test_owner_authored_workstream_is_honest_and_idempotent(runtime):
    graph = runtime.graph
    created = create_workstream_fn(
        graph, "Fund II Close", description="close the second fund",
        goal="signed docs", actor="owner:test",
    )
    assert created["created"] is True
    project = graph.get_object(created["project_id"])
    assert project.data["metadata"]["derivation_kind"] == "owner_authored"
    assert project.data["confirmed_by"] == "owner:test"
    assert project.data["seeded_from_candidate_id"] is None
    again = create_workstream_fn(graph, "  fund ii   CLOSE ", actor="owner:test")
    assert again["already_exists"] is True
    assert again["project_id"] == created["project_id"]


def test_archive_keeps_history_and_blocks_double_archive(runtime):
    graph = runtime.graph
    created = create_workstream_fn(graph, "Old Effort", actor="owner:test")
    result = archive_project_fn(
        graph, created["project_id"], actor="owner:test", reason="wrapped up",
    )
    assert result["status"] == "archived"
    assert graph.get_object(created["project_id"]).data["status"] == "archived"
    second = archive_project_fn(graph, created["project_id"], actor="owner:test")
    assert second["ok"] is False and second["reason"] == "not_active"


def test_association_plus_mention_routes_confidently(runtime):
    graph = runtime.graph
    company = _entity(graph, "Untapped Capital")
    project = create_workstream_fn(graph, "Untapped Capital", actor="owner:test")
    associate_workstream_fn(
        graph, project["project_id"], company.id, role="company", actor="owner:test",
    )
    person = _entity(graph, "Maggie Lin", entity_type="person")
    associate_workstream_fn(
        graph, project["project_id"], person.id, role="lp", actor="owner:test",
    )
    thread = _thread(
        graph, subject="Untapped Capital LP letter draft",
        participants=[("maggie@fund.example", person.id)],
        suffix="assoc",
    )
    runtime.run_until_idle()

    derived = derive_route_fn(graph, thread.id)
    assert derived["decision"] == "route"
    top = derived["candidates"][0]
    assert top["project_id"] == project["project_id"]
    assert "association" in top["signals"]
    assert "alias_mention" in top["signals"]
    assert top["score_milli"] >= AUTO_ROUTE_THRESHOLD_MILLI

    routed = route_pending_fn(graph)
    assert [row["item_ref"] for row in routed["routed"]] == [thread.id]
    relation = [
        r for r in graph.relations(source=thread.id, type="routed_to")
        if not (r.data or {}).get("removed")
    ][0]
    assert relation.data["routed_by"] == "agent:router"
    assert relation.data["confidence_milli"] == top["score_milli"]
    assert "router:" in relation.data["routing_provenance"]

    # Idempotent: a second pass finds nothing new to do.
    again = route_pending_fn(graph)
    assert again["routed"] == []


def test_ambiguity_stays_unfiled_never_force_filed(runtime):
    graph = runtime.graph
    entity_a = _entity(graph, "Acme")
    entity_b = _entity(graph, "Beacon")
    project_a = create_workstream_fn(graph, "Acme", actor="owner:test")
    project_b = create_workstream_fn(graph, "Beacon", actor="owner:test")
    person = _entity(graph, "Sam Doe", entity_type="person")
    for pid in (project_a["project_id"], project_b["project_id"]):
        associate_workstream_fn(graph, pid, person.id, role="contact", actor="owner:test")
    del entity_a, entity_b
    thread = _thread(
        graph, subject="quarterly sync",
        participants=[("sam@both.example", person.id)],
        suffix="ambig",
    )
    runtime.run_until_idle()

    derived = derive_route_fn(graph, thread.id)
    # The shared participant scores both projects identically: no margin,
    # no filing.
    assert derived["decision"] == "unfiled"
    result = route_pending_fn(graph)
    assert result["routed"] == []
    assert result["unfiled"] >= 1
    tray = unrouted_items_fn(graph)
    assert any(row["item_ref"] == thread.id for row in tray["items"])
    assert tray["total"] >= 1


def test_no_signal_item_stays_unfiled(runtime):
    graph = runtime.graph
    create_workstream_fn(graph, "Solo Project", actor="owner:test")
    thread = _thread(graph, subject="lunch?", suffix="nosig")
    runtime.run_until_idle()
    derived = derive_route_fn(graph, thread.id)
    assert derived["decision"] == "unfiled"
    assert derived["candidates"] == []


def test_owner_unroute_pins_item_and_correction_teaches(runtime):
    graph = runtime.graph
    company = _entity(graph, "BabyAGI")
    project = create_workstream_fn(graph, "BabyAGI", actor="owner:test")
    other = create_workstream_fn(graph, "Research Notes", actor="owner:test")
    associate_workstream_fn(
        graph, project["project_id"], company.id, role="project", actor="owner:test",
    )
    person = _entity(graph, "Kai Ito", entity_type="person")
    associate_workstream_fn(
        graph, project["project_id"], person.id, role="collaborator", actor="owner:test",
    )
    thread = _thread(
        graph, subject="BabyAGI release plan",
        participants=[("kai@example.dev", person.id)],
        suffix="pin",
    )
    runtime.run_until_idle()

    assert derive_route_fn(graph, thread.id)["decision"] == "route"
    route_pending_fn(graph)

    # The owner un-files it: the router must not re-file it on the next pass.
    correction = correct_routing_fn(
        graph, thread.id, to_project_id=None, actor="owner:test",
        reason="not project material",
    )
    assert correction["ok"] is True
    derived = derive_route_fn(graph, thread.id)
    assert derived["decision"] == "owner_unfiled"
    assert route_pending_fn(graph)["routed"] == []

    # A sibling thread sharing the participant: the away-correction drags
    # the score down for the corrected-from project.
    sibling = _thread(
        graph, subject="BabyAGI release plan part 2",
        participants=[("kai@example.dev", person.id)],
        suffix="pin2",
    )
    runtime.run_until_idle()
    sibling_derived = derive_route_fn(graph, sibling.id)
    top = sibling_derived["candidates"][0]
    assert top["project_id"] == project["project_id"]
    assert "correction_away" in top["signals"]

    # And a toward-correction teaches the other direction.
    correct_routing_fn(
        graph, thread.id, to_project_id=other["project_id"],
        actor="owner:test", reason="belongs in research",
    )
    retaught = derive_route_fn(graph, sibling.id)
    toward = {
        row["project_id"]: row["signals"] for row in retaught["candidates"]
    }
    assert "correction_toward" in toward.get(other["project_id"], {})


def test_owner_manual_route_wins_and_router_skips_it(runtime):
    graph = runtime.graph
    project = create_workstream_fn(graph, "Manual Home", actor="owner:test")
    thread = _thread(graph, subject="anything at all", suffix="manual")
    runtime.run_until_idle()
    routed = route_item_fn(
        graph, thread.id, project["project_id"], actor="owner:test",
        provenance="owner filed it",
    )
    assert routed["ok"] is True
    assert derive_route_fn(graph, thread.id)["decision"] == "already_routed"
    tray = unrouted_items_fn(graph)
    assert all(row["item_ref"] != thread.id for row in tray["items"])


def test_description_edit_is_a_patch_with_rationale(runtime):
    graph = runtime.graph
    project = create_workstream_fn(graph, "Describe Me", actor="owner:test")
    result = describe_project_fn(
        graph, project["project_id"], "now with substance", actor="owner:test",
    )
    assert result["ok"] is True
    assert graph.get_object(project["project_id"]).data["description"] == (
        "now with substance"
    )


def test_owner_task_and_candidate_acceptance_are_canonical(runtime):
    graph = runtime.graph
    project = create_workstream_fn(graph, "Task Home", actor="owner:test")
    task = create_task_fn(
        graph, "send the LP letter", project_id=project["project_id"],
        priority="high", due_at="2026-07-21T09:00:00+00:00",
        owner_ref="owner:test",
    )
    assert task.data["status"] == "active"
    assert task.data["due_at"] == "2026-07-21T09:00:00+00:00"

    candidate = graph.add_object("task_candidate", {
        "candidate_identity": "cand_x1",
        "text": "review the deck from Shrey",
        "confidence": 0.8,
        "evidence_id": "evidence#1",
        "evidence_identity": "sha_evidence_1",
        "revision_id": "rev#1",
        "extraction_record_id": "rec#1",
        "extractor_id": "extractor_test",
        "extractor_version": "1",
        "extraction_config_id": "cfg#1",
        "status": "candidate",
    })
    accepted = accept_task_candidate_fn(
        graph, candidate.id, project_id=project["project_id"],
        priority="medium", owner_ref="owner:test",
    )
    assert accepted.data["status"] == "active"
    assert candidate.id in accepted.data["source_observation_ids"]
    # Idempotent: accepting again returns the same task.
    assert accept_task_candidate_fn(graph, candidate.id).id == accepted.id

    rows = project_tasks_fn(graph, project_id=project["project_id"])["tasks"]
    assert {row["title"] for row in rows} == {
        "send the LP letter", "review the deck from Shrey",
    }
    assert all(row["project_id"] == project["project_id"] for row in rows)
    # high before medium; both active first.
    assert rows[0]["title"] == "send the LP letter"
