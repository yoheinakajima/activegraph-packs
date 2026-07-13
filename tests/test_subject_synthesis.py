"""ADR 0043: determinism floors, synthesis proposes, verdicts promote."""

from __future__ import annotations

import hashlib

from activegraph import Graph, Runtime

from packs.activity_normalizer import pack as normalizer_pack
from packs.llm_provider import parse_json_payload
from packs.projects import pack as projects_pack
from packs.projects.tools import project_projects_fn, review_project_candidate_fn
from packs.subject_profile import pack as subject_profile_pack
from packs.subject_profile.tools import review_subject_fact_fn
from packs.subject_synthesis import pack as synthesis_pack
from packs.subject_synthesis.engine import (
    commit_subject_synthesis_fn,
    pending_subject_synthesis_requests_fn,
    perform_subject_synthesis,
    prepare_subject_synthesis_fn,
    request_subject_synthesis_fn,
)


def _runtime():
    graph = Graph()
    runtime = Runtime(graph)
    for pack in (normalizer_pack, subject_profile_pack, projects_pack, synthesis_pack):
        runtime.load_pack(pack)
    runtime.run_until_idle()
    return graph, runtime


def _owner_evidence(graph, runtime, text="I am the founder of Untapped Capital and I build BabyAGI."):
    digest = hashlib.sha256(text.encode()).hexdigest()
    item = graph.add_object("acquired_item", {
        "source_surface_id": "assistant_self_summary", "provider_item_id": "s1",
        "dedup_key": digest[:12], "source_ref": "owner://summary",
        "source_hash": digest, "provider_time": None, "replay_mode": "inline",
        "replay_payload_ref": text, "replay_payload_hash": digest,
        "media_type": "text/plain", "importer_id": "test", "importer_version": "1",
    })
    graph.add_object("acquired_content", {
        "acquired_item_id": item.id, "normalized_content": text,
        "normalized_metadata": {"subject_scope": "owner_profile"},
        "source_category": "local_knowledge", "connection_path": "manual",
        "is_fixture": True,
    })
    runtime.run_until_idle()
    return graph.objects(type="activity_evidence")[0]


def _statement_fact(graph, evidence, value, attribute="profile_statement"):
    return graph.add_object("subject_fact", {
        "fact_identity": f"fact-{attribute}-{hashlib.sha256(value.encode()).hexdigest()[:10]}",
        "subject_ref": "owner", "attribute": attribute, "value": value,
        "text": value, "status": "promoted", "confidence": 0.9, "trust": 0.9,
        "candidate_id": None, "annotation_id": None, "evidence_id": evidence.id,
        "source_surface_id": "assistant_self_summary", "verdict_id": None,
        "supersedes_fact_id": None, "metadata": {},
    })


def test_parse_json_payload_tolerates_prose_and_fences():
    assert parse_json_payload('{"a": 1}') == {"a": 1}
    assert parse_json_payload('Here you go:\n```json\n{"a": 1}\n```\nDone.') == {"a": 1}
    assert parse_json_payload('preamble {"a": {"b": 2}} trailing {"c": 3}') == {"a": {"b": 2}}
    assert parse_json_payload("no json here") is None
    assert parse_json_payload("") is None


def test_request_is_idempotent_while_open():
    graph, runtime = _runtime()
    first = request_subject_synthesis_fn(graph, reason="test")
    second = request_subject_synthesis_fn(graph, reason="again")
    assert first["created"] is True and second["created"] is False
    assert first["request_id"] == second["request_id"]
    assert pending_subject_synthesis_requests_fn(graph) == [first["request_id"]]


def test_synthesis_proposals_cite_or_die_and_stay_verdict_gated():
    graph, runtime = _runtime()
    evidence = _owner_evidence(graph, runtime)
    fact = _statement_fact(
        graph, evidence,
        "Yohei Nakajima is a builder-investor and founder of Untapped Capital.",
    )
    request = request_subject_synthesis_fn(graph)
    payload = prepare_subject_synthesis_fn(graph, request["request_id"])
    assert payload["status"] == "prepared"
    assert payload["facts"][0]["class"] == "narrative"

    outcome = {
        "identity": [
            {"attribute": "company", "value": "Untapped Capital",
             "refs": [fact.id], "rationale": "you call yourself its founder"},
            {"attribute": "company", "value": "Uncited Corp",
             "refs": [], "rationale": "no citation"},
            {"attribute": "company", "value": "Bogus Ref Inc",
             "refs": ["not-a-ref"], "rationale": "unknown citation"},
            {"attribute": "email", "value": "leak@example.com",
             "refs": [fact.id], "rationale": "never allowed"},
        ],
        "projects": [
            {"name": "BabyAGI", "refs": [fact.id],
             "rationale": "you say you build it"},
        ],
        "noise": [{"name": "Black Friday Sale", "reason": "campaign label"}],
        "model": "claude-test", "error": None,
    }
    committed = commit_subject_synthesis_fn(graph, request["request_id"], payload, outcome)
    runtime.run_until_idle()
    assert committed["ok"] is True
    assert committed["identity_candidates"] == 1
    assert committed["project_candidates"] == 1
    assert committed["dropped_uncited"] == 2  # uncited + unknown-ref

    # Identity proposal is a CANDIDATE — promotion still takes the verdict.
    candidate = next(
        obj for obj in graph.objects(type="profile_candidate")
        if (obj.data or {}).get("attribute") == "company"
    )
    assert candidate.data["metadata"]["projector"] == "subject_synthesis.profile"
    assert not [
        obj for obj in graph.objects(type="subject_fact")
        if (obj.data or {}).get("attribute") == "company"
    ]
    review_subject_fact_fn(graph, candidate.id, "confirm")
    runtime.run_until_idle()
    promoted = [
        obj for obj in graph.objects(type="subject_fact")
        if (obj.data or {}).get("attribute") == "company"
        and (obj.data or {}).get("status") == "promoted"
    ]
    assert len(promoted) == 1

    # The project proposal is a candidate with cited sources; the run
    # receipt records inputs, proposals, and the deliberate noise.
    [project_candidate] = [
        row for row in project_projects_fn(graph)["candidates"]
        if row["name"] == "BabyAGI"
    ]
    assert project_candidate["kind"] == "synthesized"
    assert fact.id in project_candidate["sources"]
    [run] = graph.objects(type="subject_synthesis_run")
    assert run.data["status"] == "completed"
    assert run.data["proposed"]["identity_candidates"] == 1
    assert run.data["noise"][0]["name"] == "Black Friday Sale"
    request_obj = graph.get_object(request["request_id"])
    assert request_obj.data["status"] == "completed"
    assert request_obj.data["run_id"] == run.id


def test_dismissed_projects_never_resurrect_through_synthesis():
    graph, runtime = _runtime()
    evidence = _owner_evidence(graph, runtime)
    fact = _statement_fact(graph, evidence, "I run the AI Job board.")
    graph.add_object("project_candidate", {
        "candidate_identity": "c-old", "name": "AI Job",
        "kind": "fact_seeded", "score_milli": 900, "sources": [fact.id],
        "rationale": "old", "status": "dismissed", "project_id": None,
        "metadata": {},
    })
    request = request_subject_synthesis_fn(graph)
    payload = prepare_subject_synthesis_fn(graph, request["request_id"])
    assert "ai job" in payload["dismissed_projects"]
    outcome = {
        "identity": [], "noise": [],
        "projects": [{"name": "AI Job", "refs": [fact.id], "rationale": "again"}],
        "model": "claude-test", "error": None,
    }
    committed = commit_subject_synthesis_fn(graph, request["request_id"], payload, outcome)
    assert committed["project_candidates"] == 0
    rows = [
        row for row in project_projects_fn(graph)["candidates"]
        if row["name"] == "AI Job"
    ]
    assert len(rows) == 1 and rows[0]["status"] == "dismissed"


def test_keyless_synthesis_fails_honestly_and_settles_the_request():
    graph, runtime = _runtime()
    evidence = _owner_evidence(graph, runtime)
    _statement_fact(graph, evidence, "I build things.")
    request = request_subject_synthesis_fn(graph)
    payload = prepare_subject_synthesis_fn(graph, request["request_id"])
    outcome = perform_subject_synthesis(payload)  # no provider configured
    assert outcome["error"] == "synthesis_provider_unavailable"
    committed = commit_subject_synthesis_fn(graph, request["request_id"], payload, outcome)
    assert committed["ok"] is False
    request_obj = graph.get_object(request["request_id"])
    assert request_obj.data["status"] == "failed"
    [run] = graph.objects(type="subject_synthesis_run")
    assert run.data["status"] == "failed"
    assert pending_subject_synthesis_requests_fn(graph) == []
