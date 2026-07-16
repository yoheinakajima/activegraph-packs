"""ADR 0046: the setup draft is the reviewed gateway.

Covers deterministic packing with recorded refs, cite-or-drop at the draft
boundary, predictions recorded before verdicts, owner edits superseding
without inflating accuracy, routing every section through its canonical
pipeline at submission, restartable partial commits, versioning that never
mutates a submitted draft, and the zero-key deterministic floor.
"""

from __future__ import annotations

import hashlib

import pytest

from activegraph import Graph, Runtime

from packs.activity_normalizer import pack as normalizer_pack
from packs.connector_control import pack as connector_control_pack
from packs.projects import pack as projects_pack
from packs.subject_profile import pack as subject_profile_pack
from packs.subject_synthesis import pack as subject_synthesis_pack
from packs.subject_synthesis.draft import (
    begin_setup_draft_submission_fn,
    commit_setup_draft_fn,
    compose_deterministic_draft_fn,
    current_setup_draft_fn,
    defer_setup_draft_fn,
    merge_setup_project_items_fn,
    prepare_setup_draft_fn,
    project_setup_draft_fn,
    request_setup_draft_fn,
    resubmit_setup_draft_fn,
    review_setup_item_fn,
    complete_setup_draft_submission_fn,
)


@pytest.fixture
def runtime():
    rt = Runtime(Graph())
    rt.load_pack(normalizer_pack)
    rt.load_pack(connector_control_pack)
    rt.load_pack(subject_profile_pack)
    rt.load_pack(projects_pack)
    rt.load_pack(subject_synthesis_pack)
    rt.run_until_idle()
    return rt


def _owner_evidence(graph, runtime):
    text = "I am Yohei Nakajima, GP at Untapped Capital."
    digest = hashlib.sha256(text.encode()).hexdigest()
    item = graph.add_object("acquired_item", {
        "source_surface_id": "identity_seed", "provider_item_id": "seed-1",
        "dedup_key": "seed-1", "source_ref": "seed", "source_hash": digest,
        "provider_time": None, "replay_mode": "inline",
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


def _fact(graph, evidence, attribute, value):
    return graph.add_object("subject_fact", {
        "fact_identity": f"fact-{attribute}-{value}",
        "subject_ref": "owner", "attribute": attribute, "value": value,
        "text": value, "status": "promoted", "confidence": 0.9, "trust": 0.9,
        "candidate_id": None, "annotation_id": None, "evidence_id": evidence.id,
        "source_surface_id": "identity_seed", "verdict_id": None,
        "supersedes_fact_id": None, "metadata": {},
    })


def _research_run(graph):
    return graph.add_object("web_research_run", {
        "run_identity": "run-research", "source_surface_id": "web_research:owner",
        "plan_identity": "plan-1", "queries": ['"Yohei Nakajima"'],
        "status": "completed",
        "findings": [
            {"claim": "GP at Untapped Capital", "url": "https://untapped.vc/team"},
            {"claim": "hostile claim", "url": "https://evil.test",
             "injection_flags": ["instruction_override"]},
        ],
        "urls_planned": ["https://untapped.vc/team"], "urls_ingested": 1,
        "model": "m", "calls": 1, "rounds_executed": 1,
        "stop_reason": "frontier_exhausted", "error": None, "metadata": {},
    })


def _leaf(graph, ref="leaf-1"):
    request = graph.add_object("comprehension_request", {
        "request_identity": "req-1", "recipe_id": "gmail_sent_v1",
        "service": "gmail", "source_surface_id": "gmail:owner",
        "plan_identity": "", "status": "completed", "requested_by": "test",
        "counts": {"items": 1, "batches": 1, "batches_done": 1, "leaves": 1},
        "coverage": {"selected": 1}, "item_refs": ["m1"], "metadata": {},
    })
    return graph.add_object("source_item_summary", {
        "summary_identity": ref, "request_id": request.id,
        "recipe_id": "gmail_sent_v1", "item_ref": "m1",
        "evidence_refs": ["evidence-m1"], "batch_index": 0,
        "fields": {
            "authored_intent": "coordinating the Atlas beta launch",
            "projects": ["Atlas"], "topics": ["launch"],
            "people": [{"name": "Jane Doe", "relationship": "collaborator"}],
        },
        "model": "fast-test", "injection_flags": [], "metadata": {},
    })


def _draft_outcome(payload):
    """A strong-pass outcome citing real included refs, plus uncited noise."""
    fact_ref = payload["facts"][0]["ref"]
    research_ref = payload["research"][0]["ref"]
    leaf_ref = payload["comprehension"][0]["ref"]
    return {
        "ok": True, "model": "reasoning-test",
        "sections": {
            "identity": [
                {"attribute": "role", "value": "General Partner",
                 "refs": [fact_ref, research_ref],
                 "rationale": "confirmed fact corroborated by public page",
                 "confidence": 0.9, "uncertainty": ""},
                {"attribute": "company", "value": "Uncited Corp", "refs": [],
                 "rationale": "invented"},
            ],
            "narrative": [
                {"statement": "Builds tools that make agents trustworthy.",
                 "refs": [leaf_ref], "rationale": "recurring theme in sent mail"},
            ],
            "instructions": [
                {"instruction": "Keep updates short and bullet-heavy.",
                 "refs": [leaf_ref], "rationale": "matches authored style"},
            ],
            "projects": [
                {"name": "Atlas", "description": "The beta launch you coordinate.",
                 "status_note": "active", "people": ["Jane Doe"],
                 "refs": [leaf_ref], "rationale": "dominant thread in sent mail"},
                {"name": "Zephyr", "description": "…", "refs": [leaf_ref],
                 "rationale": "secondary thread"},
            ],
            "people": [
                {"name": "Jane Doe", "relationship": "Atlas collaborator",
                 "refs": [leaf_ref], "rationale": "recurring recipient"},
            ],
            "access": [
                {"question_class": "collaborator questions", "source": "gmail",
                 "strategy": "search sent mail for the person's name",
                 "refs": [leaf_ref], "rationale": "sent mail carries the context"},
            ],
        },
        "response_sample": "{}", "response_length": 10, "error": None,
    }


def _composed(runtime):
    graph = runtime.graph
    evidence = _owner_evidence(graph, runtime)
    _fact(graph, evidence, "name", "Yohei Nakajima")
    _fact(graph, evidence, "company", "Untapped Capital")
    _research_run(graph)
    _leaf(graph)
    request = request_setup_draft_fn(graph)
    payload = prepare_setup_draft_fn(graph, request["request_id"])
    committed = commit_setup_draft_fn(
        graph, request["request_id"], payload, _draft_outcome(payload)
    )
    runtime.run_until_idle()
    return graph, payload, committed


def test_prepare_packs_every_source_with_budgets_and_recorded_refs(runtime):
    graph = runtime.graph
    evidence = _owner_evidence(graph, runtime)
    _fact(graph, evidence, "name", "Yohei Nakajima")
    _research_run(graph)
    _leaf(graph)
    request = request_setup_draft_fn(graph)
    assert request["ok"]
    # Idempotent while open.
    assert request_setup_draft_fn(graph)["already_open"] is True

    payload = prepare_setup_draft_fn(graph, request["request_id"])
    assert payload["kind"] == "setup_draft"
    assert [f["value"] for f in payload["facts"]] == ["Yohei Nakajima"]
    # Injection-flagged research findings never reach derivation.
    assert [r["claim"] for r in payload["research"]] == ["GP at Untapped Capital"]
    assert payload["comprehension"][0]["fields"]["projects"] == ["Atlas"]
    # Deterministic packing is recorded per source, and included refs are
    # the audit trail of what the model could actually read.
    for source in ("facts", "research", "comprehension"):
        assert payload["packing"][source]["included"] >= 1
        assert payload["packing"][source]["dropped"] == 0
    assert payload["included_refs"]
    assert payload["research_coverage"][0]["stop_reason"] == "frontier_exhausted"


def test_commit_routes_sections_drops_uncited_and_records_predictions(runtime):
    graph, payload, committed = _composed(runtime)
    assert committed["ok"] is True
    assert committed["dropped_uncited"] == 1  # "Uncited Corp" died at the boundary

    projection = project_setup_draft_fn(graph)
    draft = projection["draft"]
    assert draft["version"] == 1
    assert draft["status"] == "proposed"
    assert projection["resolved"] is False
    by_section = {}
    for item in projection["items"]:
        by_section.setdefault(item["section"], []).append(item)
    assert len(by_section["identity"]) == 1
    assert len(by_section["projects"]) == 2
    assert by_section["people"][0]["destination"] == "entity_relationship"
    assert by_section["access"][0]["destination"] == "access_hint"
    for item in projection["items"]:
        assert item["evidence_refs"]  # no uncited item exists at all
        assert item["predicted_verdict"] in ("accept", "reject", "edit", "defer")
        assert item["verdict"] is None  # prediction precedes any verdict
    # Identity items carry their owner-attested candidate; the strong pass
    # minted it as a candidate, never a fact.
    assert by_section["identity"][0]["status"] == "proposed"
    assert not graph.objects(type="information_access_hint")


def test_review_edit_merge_submit_promotes_through_canonical_paths(runtime):
    graph, payload, _ = _composed(runtime)
    projection = project_setup_draft_fn(graph)
    items = {item["section"]: [] for item in projection["items"]}
    for item in projection["items"]:
        items[item["section"]].append(item)

    review_setup_item_fn(graph, items["identity"][0]["id"], "accept")
    review_setup_item_fn(
        graph, items["narrative"][0]["id"], "edit",
        edited_value={"value": "I build agent infrastructure in public."},
    )
    review_setup_item_fn(graph, items["instructions"][0]["id"], "accept")
    review_setup_item_fn(graph, items["people"][0]["id"], "accept")
    review_setup_item_fn(graph, items["access"][0]["id"], "accept")
    merged = merge_setup_project_items_fn(
        graph, [items["projects"][0]["id"], items["projects"][1]["id"]],
        "Atlas Platform",
    )
    assert merged["ok"] and merged["merged"] == 1

    draft_id = projection["draft"]["id"]
    begun = begin_setup_draft_submission_fn(graph, draft_id)
    assert begun["ok"] and begun["staged_declarations"] >= 3
    runtime.run_until_idle()  # the host drain: declarations normalize
    completed = complete_setup_draft_submission_fn(graph, draft_id)
    runtime.run_until_idle()  # verdicts settle through pack behaviors
    assert completed["ok"] is True, completed
    assert completed["status"] == "submitted"
    assert completed["resolution"] == "submitted"

    # Identity → promoted subject fact through the pack pipeline.
    facts = {
        (f.data["attribute"], f.data["value"])
        for f in graph.objects(type="subject_fact")
        if f.data["status"] == "promoted"
    }
    assert ("role", "General Partner") in facts
    # Narrative EDIT became the owner's declaration, not the model's text.
    assert ("profile_statement", "I build agent infrastructure in public.") in facts
    assert ("instruction", "Keep updates short and bullet-heavy.") in facts
    assert ("person", "Jane Doe") in facts
    # The owner-declaration verdicts carry the no-prediction-win marker.
    declaration_verdicts = [
        v for v in graph.objects(type="subject_fact_verdict")
        if (v.data.get("metadata") or {}).get("owner_declaration")
    ]
    assert len(declaration_verdicts) >= 3

    # Projects → one canonical project from the owner-merged item.
    [project] = graph.objects(type="project")
    assert project.data["name"] == "Atlas Platform"
    assert project.data["status"] == "active"
    assert "beta launch" in project.data["description"]

    # Access → a hint object, never memory or identity.
    [hint] = graph.objects(type="information_access_hint")
    assert hint.data["source"] == "gmail"
    assert hint.data["strategy"].startswith("search sent mail")

    # Resolution is durable server state.
    assert project_setup_draft_fn(graph)["resolved"] is True


def test_rejected_items_reject_their_candidates_and_defer_is_durable(runtime):
    graph, payload, _ = _composed(runtime)
    projection = project_setup_draft_fn(graph)
    identity = next(i for i in projection["items"] if i["section"] == "identity")
    review_setup_item_fn(graph, identity["id"], "reject")
    draft_id = projection["draft"]["id"]
    # Undecided items refuse a bare submission (ADR 0051 §2): deferral is an
    # explicit acknowledgment, never a silent side effect.
    refused = begin_setup_draft_submission_fn(graph, draft_id)
    assert refused["ok"] is False
    assert refused["reason"] == "undecided_items"
    assert refused["route"] == "setup_review"
    begun = begin_setup_draft_submission_fn(
        graph, draft_id, defer_undecided=True,
    )
    assert begun["ok"] is True
    runtime.run_until_idle()
    completed = complete_setup_draft_submission_fn(graph, draft_id)
    runtime.run_until_idle()
    assert completed["status"] == "submitted"
    # The rejected identity never became a fact; its candidate is settled.
    assert not any(
        f.data["value"] == "General Partner" and f.data["attribute"] == "role"
        for f in graph.objects(type="subject_fact")
    )
    # Unresolved items were explicitly deferred, not lost.
    projection = project_setup_draft_fn(graph)
    deferred = [i for i in projection["items"] if i["status"] == "deferred"]
    assert deferred
    # The acceptance recorded its horizon and the known delta dispositions.
    head = current_setup_draft_fn(graph)
    assert head.data["metadata"].get("accepted_horizon") is not None
    assert "delta_dispositions" in head.data["metadata"]

    # An accepted head is immutable: later composition lands as ONE
    # cumulative understanding delta against it (ADR 0051 §3), never a
    # spontaneous superseding version.
    head_before = current_setup_draft_fn(graph)
    assert head_before.data["status"] == "submitted"
    second = compose_deterministic_draft_fn(graph)
    assert second["frozen_head"] == head_before.id
    head_after = current_setup_draft_fn(graph)
    assert head_after.id == head_before.id
    assert graph.get_object(head_before.id).data["status"] == "submitted"
    assert second["ok"]


def test_partial_commit_stays_visible_and_resubmit_retries(runtime):
    graph, payload, _ = _composed(runtime)
    projection = project_setup_draft_fn(graph)
    project_item = next(
        i for i in projection["items"]
        if i["section"] == "projects" and i["proposed"]["name"] == "Atlas"
    )
    review_setup_item_fn(graph, project_item["id"], "accept")
    # Sabotage: the underlying candidate vanishes mid-flight.
    item_obj = graph.get_object(project_item["id"])
    graph.patch_object(item_obj.id, {"candidate_ref": None})

    draft_id = projection["draft"]["id"]
    begin_setup_draft_submission_fn(graph, draft_id)
    runtime.run_until_idle()
    completed = complete_setup_draft_submission_fn(graph, draft_id)
    assert completed["ok"] is False
    assert completed["status"] == "partial"
    refreshed = project_setup_draft_fn(graph)
    failed = [i for i in refreshed["items"] if i["status"] == "commit_failed"]
    [failure] = failed
    assert "candidate" in failure["commit_error"]
    # Partial is itself a durable resolution the ceremony may proceed on.
    assert refreshed["resolved"] is True

    # Repair and resubmit retries exactly the failed item.
    candidate = next(
        obj for obj in graph.objects(type="project_candidate")
        if obj.data["name"] == "Atlas"
    )
    graph.patch_object(failure["id"], {"candidate_ref": candidate.id})
    resubmitted = resubmit_setup_draft_fn(graph, draft_id)
    runtime.run_until_idle()
    assert resubmitted["ok"] is True
    assert resubmitted["status"] == "submitted"
    [project] = graph.objects(type="project")
    assert project.data["name"] == "Atlas"


def test_zero_key_deterministic_draft_reaches_the_same_gate(runtime):
    graph = runtime.graph
    evidence = _owner_evidence(graph, runtime)
    # A pending identity candidate and a proposed project candidate — the
    # deterministic floor composes them into the identical review shape.
    graph.add_object("profile_candidate", {
        "candidate_identity": "c-handle", "text": "handle @yoheinakajima",
        "confidence": 0.8, "evidence_id": evidence.id,
        "evidence_identity": evidence.data["evidence_identity"],
        "revision_id": evidence.data["revision_id"],
        "extraction_record_id": "run", "extractor_id": "test",
        "extractor_version": "1", "extraction_config_id": "cfg",
        "status": "candidate", "invalidation_reason": None,
        "metadata": {}, "attribute": "handle", "value": "yoheinakajima",
    })
    graph.add_object("project_candidate", {
        "candidate_identity": "p-atlas", "name": "Atlas", "kind": "fact_seeded",
        "score_milli": 900, "sources": [evidence.id],
        "rationale": "confirmed fact names it", "status": "proposed",
        "description": "", "project_id": None, "metadata": {},
    })
    composed = compose_deterministic_draft_fn(graph)
    assert composed["ok"] and composed["items"] == 2
    projection = project_setup_draft_fn(graph)
    assert projection["draft"]["source"] == "deterministic"
    for item in projection["items"]:
        review_setup_item_fn(graph, item["id"], "accept")
    draft_id = projection["draft"]["id"]
    begin_setup_draft_submission_fn(graph, draft_id)
    runtime.run_until_idle()
    completed = complete_setup_draft_submission_fn(graph, draft_id)
    runtime.run_until_idle()
    assert completed["status"] == "submitted"
    assert graph.objects(type="project")
    facts = {
        (f.data["attribute"], f.data["value"])
        for f in graph.objects(type="subject_fact")
        if f.data["status"] == "promoted"
    }
    assert ("handle", "yoheinakajima") in facts

    # An empty store still reaches a resolvable draft: never a dead end.
    fresh = Runtime(Graph())
    fresh.load_pack(normalizer_pack)
    fresh.load_pack(connector_control_pack)
    fresh.load_pack(subject_profile_pack)
    fresh.load_pack(projects_pack)
    fresh.load_pack(subject_synthesis_pack)
    fresh.run_until_idle()
    empty = compose_deterministic_draft_fn(fresh.graph)
    assert empty["ok"] and empty["items"] == 0
    resolution = defer_setup_draft_fn(fresh.graph, empty["draft_id"])
    assert resolution["resolution"] == "deferred"
    assert project_setup_draft_fn(fresh.graph)["resolved"] is True


def test_perform_reads_the_real_llm_response_shape(runtime):
    """Regression for the first live keyed run: the runtime provider returns
    LLMResponse(raw_text=...), and reading .text silently blanked every
    strong pass — draft v1 committed with zero proposals. perform must read
    the real shape, and an empty strong pass must FAIL the request so the
    deterministic floor composes instead of an empty gateway."""
    import json
    from decimal import Decimal

    from activegraph.llm.types import LLMResponse

    from packs.subject_synthesis.draft import perform_setup_draft

    graph = runtime.graph
    evidence = _owner_evidence(graph, runtime)
    _fact(graph, evidence, "role", "General Partner")
    request = request_setup_draft_fn(graph)
    payload = prepare_setup_draft_fn(graph, request["request_id"])
    fact_ref = payload["facts"][0]["ref"]

    def _response(raw: str) -> LLMResponse:
        return LLMResponse(
            raw_text=raw, parsed=None, input_tokens=10, output_tokens=10,
            cost_usd=Decimal("0"), latency_seconds=0.1, model="m",
            finish_reason="end_turn",
        )

    class RealShapeProvider:
        def __init__(self, raw: str):
            self.raw = raw

        def complete(self, **kwargs):
            return _response(self.raw)

    body = json.dumps({
        "identity": [{"attribute": "role", "value": "Managing Partner",
                      "refs": [fact_ref], "rationale": "sent mail"}],
    })
    outcome = perform_setup_draft(
        payload, provider=RealShapeProvider(body), model="m"
    )
    assert outcome["ok"] is True
    assert outcome["response_length"] == len(body)
    assert outcome["sections"]["identity"], "raw_text must reach the parser"

    # The live failure mode: a completed call whose text reads empty.
    empty = perform_setup_draft(
        payload, provider=RealShapeProvider(""), model="m"
    )
    assert empty["response_length"] == 0
    result = commit_setup_draft_fn(graph, request["request_id"], payload, empty)
    assert result["ok"] is False
    assert "empty_synthesis_response" in str(result["error"])
    assert graph.get_object(request["request_id"]).data["status"] == "failed"
    assert current_setup_draft_fn(graph) is None, "no empty draft is minted"

    # With the request failed, the deterministic floor still resolves the
    # gate — never a dead end (ADR 0046 §5).
    graph.add_object("project_candidate", {
        "candidate_identity": "p-atlas", "name": "Atlas", "kind": "fact_seeded",
        "score_milli": 900, "sources": [evidence.id],
        "rationale": "confirmed fact names it", "status": "proposed",
        "description": "", "project_id": None, "metadata": {},
    })
    composed = compose_deterministic_draft_fn(graph)
    assert composed["ok"] is True
    draft = current_setup_draft_fn(graph)
    assert draft is not None
    rows = project_setup_draft_fn(graph)["items"]
    assert rows, "the deterministic draft proposes real items"


def test_synthesis_citing_nothing_packed_fails_the_request(runtime):
    """Proposals citing only refs we never packed are a failed pass, not an
    all-dropped empty draft."""
    graph = runtime.graph
    evidence = _owner_evidence(graph, runtime)
    _fact(graph, evidence, "role", "General Partner")
    request = request_setup_draft_fn(graph)
    payload = prepare_setup_draft_fn(graph, request["request_id"])
    outcome = {
        "ok": True, "model": "m", "response_length": 120,
        "sections": {"identity": [
            {"attribute": "role", "value": "CEO", "refs": ["not-a-ref"],
             "rationale": "hallucinated"},
        ]},
        "error": None,
    }
    result = commit_setup_draft_fn(graph, request["request_id"], payload, outcome)
    assert result["ok"] is False
    assert "synthesis_cited_nothing_packed" in str(result["error"])
    assert current_setup_draft_fn(graph) is None


def test_draft_provenance_distinguishes_fallback_from_normal_zero_key(runtime):
    """Gate 3 honesty: normal zero-key success, model-assisted success, and
    keyed-failure-then-deterministic-fallback are three DIFFERENT states —
    the fallback keeps the original failure inspectable."""
    graph = runtime.graph
    evidence = _owner_evidence(graph, runtime)
    _fact(graph, evidence, "role", "General Partner")
    graph.add_object("project_candidate", {
        "candidate_identity": "p-atlas", "name": "Atlas", "kind": "fact_seeded",
        "score_milli": 900, "sources": [evidence.id],
        "rationale": "confirmed fact names it", "status": "proposed",
        "description": "", "project_id": None, "metadata": {},
    })

    # Normal zero-key composition: plain deterministic provenance.
    composed = compose_deterministic_draft_fn(graph)
    assert composed["ok"] is True
    draft = current_setup_draft_fn(graph)
    assert draft.data["coverage"]["provenance"] == "deterministic"
    assert "fallback_from" not in draft.data["coverage"]
    sources = draft.data["coverage"]["sources"]
    assert sources["identity_seed"]["status"] == "contributed"
    # Un-run sources are durable OPPORTUNITIES, not dead "skipped" ends:
    # research is always offerable; sent mail needs acquired mail first.
    assert sources["research"]["status"] == "available"
    assert sources["sent_mail"]["status"] == "unavailable"

    # New source material arrives (the convergence rule refuses a re-request
    # over an unchanged input horizon), then a failed keyed pass: the SAME
    # composer marks the degraded path and carries the original failure.
    graph.add_object("project_candidate", {
        "candidate_identity": "p-borealis", "name": "Borealis",
        "kind": "fact_seeded", "score_milli": 850, "sources": [evidence.id],
        "rationale": "a later fact names it", "status": "proposed",
        "description": "", "project_id": None, "metadata": {},
    })
    request = request_setup_draft_fn(graph)
    assert request["request_id"], "new material re-opens the synthesis request"
    graph.patch_object(request["request_id"], {
        "status": "failed", "error": "empty_synthesis_response length=0",
    })
    composed = compose_deterministic_draft_fn(graph)
    assert composed["ok"] is True
    fallback_draft = current_setup_draft_fn(graph)
    coverage = fallback_draft.data["coverage"]
    assert coverage["provenance"] == "deterministic_fallback"
    assert "empty_synthesis_response" in coverage["fallback_from"]["error"]
    assert coverage["fallback_from"]["request_ref"] == request["request_id"]


def test_source_coverage_tracks_the_full_opportunity_lifecycle(runtime):
    """Hardening round: research and the sent study are durable
    opportunities with explicit states — available → proposed → running →
    contributed — derived from the same plan/request objects the pump
    drives, so they survive navigation, hatch, and restart by construction."""
    from packs.subject_synthesis.draft import draft_source_coverage_fn

    graph = runtime.graph
    evidence = _owner_evidence(graph, runtime)
    _fact(graph, evidence, "role", "General Partner")

    baseline = draft_source_coverage_fn(graph)
    assert baseline["research"]["status"] == "available"
    assert baseline["sent_mail"]["status"] == "unavailable"

    from packs.connector_control.plans import (
        approve_ingestion_plan_fn, propose_ingestion_plan_fn,
    )

    research = propose_ingestion_plan_fn(
        graph, source_surface_id="web_research:owner", service="web_research",
        account_ref="owner", family="documents",
        window={"kind": "recent_items", "days": None},
        derivation={"basis": "service_default", "summary": "seed queries"},
        caps={"max_items": 20, "max_pages": 3},
    )["plan"]
    assert draft_source_coverage_fn(graph)["research"]["status"] == "proposed"
    assert draft_source_coverage_fn(graph)["research"]["plan_ref"]

    approve_ingestion_plan_fn(
        graph, plan_ref=research.data["plan_identity"], approved_by="owner",
    )
    assert draft_source_coverage_fn(graph)["research"]["status"] == "running"

    # A fulfilled gmail backfill opens the sent-study opportunity even
    # though the owner never saw (or left) the connection panel.
    backfill = propose_ingestion_plan_fn(
        graph, source_surface_id="surface-g", service="gmail",
        account_ref="o@x.com", family="conversation",
        window={"kind": "recent_days", "days": 30},
        derivation={"basis": "volume_only", "summary": "recent mail"},
        caps={"max_items": 250, "max_pages": 10, "page_size": 25},
    )["plan"]
    approve_ingestion_plan_fn(
        graph, plan_ref=backfill.data["plan_identity"], approved_by="owner",
    )
    graph.patch_object(backfill.id, {"status": "fulfilled"})
    assert draft_source_coverage_fn(graph)["sent_mail"]["status"] == "available"

    sent = propose_ingestion_plan_fn(
        graph, source_surface_id="surface-g", service="gmail",
        account_ref="o@x.com", family="conversation", purpose="comprehension",
        window={"kind": "recent_items", "days": None},
        derivation={"basis": "service_default", "summary": "latest sent"},
        caps={"max_items": 100, "max_pages": 4, "page_size": 25},
    )["plan"]
    assert draft_source_coverage_fn(graph)["sent_mail"]["status"] == "proposed"
    # Re-proposing the identical body is idempotent: no duplicate plan.
    again = propose_ingestion_plan_fn(
        graph, source_surface_id="surface-g", service="gmail",
        account_ref="o@x.com", family="conversation", purpose="comprehension",
        window={"kind": "recent_items", "days": None},
        derivation={"basis": "service_default", "summary": "latest sent"},
        caps={"max_items": 100, "max_pages": 4, "page_size": 25},
    )["plan"]
    assert again.data["plan_identity"] == sent.data["plan_identity"]
