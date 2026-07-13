"""ADR 0041: deferred selection execution mirrors the synchronous path."""

from __future__ import annotations

import pytest

from activegraph import Graph, Runtime

from packs.activity_normalizer import pack as normalizer_pack
from packs.semantic_extraction import SemanticExtractionSettings, pack as extraction_pack
from packs.semantic_extraction.deferred import (
    commit_selection_request_fn,
    fail_selection_request_fn,
    pending_selection_requests_fn,
    perform_selection_request,
    prepare_selection_request_fn,
)

import hashlib


def _settings(deferred: bool) -> SemanticExtractionSettings:
    # No eager pass and no seeded profile: the selection request is the
    # first and only extraction, so both paths start from the same blank
    # coverage and their outputs are directly comparable.
    return SemanticExtractionSettings(
        deferred_selection_execution=deferred,
        seed_default_profile=False,
        default_profile_facets=(),
    )


def _runtime(deferred: bool):
    graph = Graph()
    runtime = Runtime(graph)
    runtime.load_pack(normalizer_pack)
    runtime.load_pack(extraction_pack, settings=_settings(deferred))
    return graph, runtime


TEXT = "Yohei asked: can you send the deck to jane@founderco.com by Friday?"


def _evidence(graph, runtime):
    digest = hashlib.sha256(TEXT.encode()).hexdigest()
    item = graph.add_object("acquired_item", {
        "source_surface_id": "test", "provider_item_id": "m1", "dedup_key": "m1",
        "source_ref": "test://m1", "source_hash": digest, "provider_time": None,
        "replay_mode": "inline", "replay_payload_ref": TEXT,
        "replay_payload_hash": digest, "media_type": "text/plain",
        "importer_id": "test", "importer_version": "1",
    })
    graph.add_object("acquired_content", {
        "acquired_item_id": item.id, "normalized_content": TEXT,
        "normalized_metadata": {"subject_scope": "owner_profile"},
        "source_category": "local_knowledge", "connection_path": "pack",
        "is_fixture": True,
    })
    runtime.run_until_idle()
    return graph.objects(type="activity_evidence")[0]


def _request(graph, evidence):
    content = evidence.data["normalized_content"]
    exact = content
    return graph.add_object("selection_extraction_request", {
        "request_identity": f"req-{evidence.id}",
        "evidence_id": evidence.id,
        "revision_id": evidence.data["revision_id"],
        "selection_id": "sel-1",
        "selections": [{
            "start": 0, "end": len(exact),
            "exact_hash": hashlib.sha256(exact.encode()).hexdigest(),
        }],
        "requested_facets": ["question"],
        "status": "proposed",
        "run_ids": [], "annotation_ids": [], "error": None,
        "refs": [evidence.id], "metadata": {},
    })


def test_deferred_mode_leaves_requests_proposed_and_host_settles_identically():
    # Synchronous reference run.
    sync_graph, sync_rt = _runtime(deferred=False)
    sync_evidence = _evidence(sync_graph, sync_rt)
    sync_request = _request(sync_graph, sync_evidence)
    sync_rt.run_until_idle()
    sync_settled = sync_graph.get_object(sync_request.id)
    assert sync_settled.data["status"] == "completed"
    sync_annotations = sorted(
        (a.data["facet"], a.data["selector"]["exact"])
        for a in sync_graph.objects(type="semantic_annotation")
        if a.data.get("run_id") in sync_settled.data["run_ids"]
    )
    assert sync_annotations, "reference path produced annotations"

    # Deferred run: the behavior leaves the request proposed.
    graph, runtime = _runtime(deferred=True)
    evidence = _evidence(graph, runtime)
    request = _request(graph, evidence)
    runtime.run_until_idle()
    assert graph.get_object(request.id).data["status"] == "proposed"
    assert pending_selection_requests_fn(graph) == [request.id]

    settings = _settings(deferred=True)
    prepared = prepare_selection_request_fn(graph, request.id, settings=settings)
    assert prepared["status"] == "prepared"
    drafts = perform_selection_request(prepared["groups"], settings=settings)
    settled = commit_selection_request_fn(
        graph, request.id, prepared["groups"], drafts, settings=settings
    )
    runtime.run_until_idle()
    assert settled["status"] == "completed"
    final = graph.get_object(request.id)
    assert final.data["status"] == "completed"
    deferred_annotations = sorted(
        (a.data["facet"], a.data["selector"]["exact"])
        for a in graph.objects(type="semantic_annotation")
        if a.data.get("run_id") in final.data["run_ids"]
    )
    assert deferred_annotations == sync_annotations
    assert any(
        event.type == "semantic.selection_extraction_settled"
        and event.payload.get("request_id") == request.id
        and event.payload.get("status") == "completed"
        for event in graph.events
    )
    assert pending_selection_requests_fn(graph) == []


def test_deferred_failure_paths_mirror_the_behavior():
    graph, runtime = _runtime(deferred=True)
    evidence = _evidence(graph, runtime)
    request = _request(graph, evidence)
    runtime.run_until_idle()
    settings = _settings(deferred=True)

    # Revision mismatch settles as failed through the same contract.
    graph.patch_object(request.id, {"revision_id": "rev-bogus"}, rationale="test")
    result = prepare_selection_request_fn(graph, request.id, settings=settings)
    assert result["status"] == "failed"
    assert graph.get_object(request.id).data["status"] == "failed"
    assert any(
        event.type == "semantic.selection_extraction_settled"
        and event.payload.get("request_id") == request.id
        and event.payload.get("status") == "failed"
        for event in graph.events
    )

    # A perform-phase exception is host-reported through the same path.
    second = _request(graph, evidence)
    runtime.run_until_idle()
    prepared = prepare_selection_request_fn(graph, second.id, settings=settings)
    assert prepared["status"] == "prepared"
    failed = fail_selection_request_fn(graph, second.id, "ProviderError: boom")
    assert failed["status"] == "failed"
    assert graph.get_object(second.id).data["status"] == "failed"
    assert "ProviderError" in graph.get_object(second.id).data["error"]

    # Settled requests are skipped, never re-executed.
    again = prepare_selection_request_fn(graph, second.id, settings=settings)
    assert again["status"] == "skipped"
