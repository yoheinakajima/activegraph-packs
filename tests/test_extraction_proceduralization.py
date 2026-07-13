"""ADR 0029 pilot: dynamic extraction hardens, guards, and deoptimizes."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import pytest

from activegraph import Graph, Runtime
from activegraph.llm import LLMResponse
from packs.activity_normalizer import pack as normalizer_pack
from packs.core import pack as core_pack
from packs.llm_provider import ResolvedLLMProvider, clear_llm_provider, set_llm_provider
from packs.semantic_extraction import SemanticExtractionSettings, pack as semantic_pack
from packs.semantic_extraction.engine import run_profile_extraction
from packs.semantic_extraction.procedures import (
    evaluate_request_procedure_fn,
    promote_request_procedure_fn,
    synthesize_request_procedure_fn,
)
from packs.semantic_extraction.tools import update_extraction_profile_fn


class RequestReferenceProvider:
    default_model = "fixture-reference"

    def __init__(self):
        self.calls = 0

    def complete(self, **kwargs):
        self.calls += 1
        prompt = kwargs["messages"][0].content
        content = prompt.split("CONTENT:\n", 1)[1]
        has_cue = any(
            cue in content.lower()
            for cue in ("please", "can you", "could you", "send me", "let me know")
        )
        rows = [] if not has_cue else [{
            "facet": "relation_mention", "exact": content,
            "body": {"text": content, "subject": "sender", "predicate": "requests", "object": content},
            "confidence": 0.9,
        }]
        return LLMResponse(
            raw_text=json.dumps(rows), parsed=None, input_tokens=1,
            output_tokens=1, cost_usd=Decimal("0"), latency_seconds=0.0,
            model=kwargs["model"], finish_reason="stop", seed=None,
            cache_hit=False, provider_meta={}, tool_calls=None,
        )


@pytest.fixture(autouse=True)
def _provider_boundary():
    clear_llm_provider()
    yield
    clear_llm_provider()


def _runtime(tmp_path):
    settings = SemanticExtractionSettings(
        llm_model="fixture-reference",
        llm_record_dir=str(tmp_path / "records"),
    )
    runtime = Runtime(Graph(), persist_to=str(tmp_path / "procedure.db"))
    runtime.load_pack(core_pack)
    runtime.load_pack(normalizer_pack)
    runtime.load_pack(semantic_pack, settings=settings)
    runtime.run_until_idle()
    return runtime, settings


def _evidence(graph, identity: str, text: str, *, shape_version: str = "gmail.message@1"):
    digest = hashlib.sha256(text.encode()).hexdigest()
    return graph.add_object("activity_evidence", {
        "evidence_identity": f"evidence:{identity}",
        "revision_id": f"revision:{identity}", "revision_number": 1,
        "status": "current", "acquired_item_id": f"item:{identity}",
        "acquired_content_id": f"content:{identity}",
        "source_surface_id": "gmail:owner", "provider_item_id": identity,
        "dedup_key": identity, "source_ref": f"gmail:message:{identity}",
        "source_hash": digest, "content_hash": digest, "provider_time": None,
        "replay_mode": "inline", "replay_payload_ref": text,
        "replay_payload_hash": digest, "replay_complete": True,
        "media_type": "text/plain", "encoding": "utf-8",
        "retention_policy": "source_default", "acquired_at_event_id": "fixture",
        "normalized_content": text,
        "normalized_metadata": {
            "interpretation_family": "conversation", "direction": "inbound",
            "message_kind": "human", "shape_version": shape_version,
            "injection_flags": [],
        },
        "source_category": "communication", "connection_path": "composio",
        "importer_id": "gmail", "importer_version": "fixture",
        "is_fixture": True, "supersedes_evidence_id": None,
    })


def _accepted_reference(graph, evidence, text: str):
    annotation = graph.add_object("semantic_annotation", {
        "annotation_identity": f"annotation:{evidence.data['evidence_identity']}",
        "facet": "relation_mention",
        "body": {"text": text, "subject": "sender", "predicate": "requests", "object": text},
        "evidence_id": evidence.id,
        "evidence_identity": evidence.data["evidence_identity"],
        "revision_id": evidence.data["revision_id"],
        "selector": {"kind": "char_span", "start": 0, "end": len(text), "exact": text},
        "extractor_id": "semantic.llm", "extractor_version": "0.1.0",
        "config_hash": "0" * 64, "confidence": 0.9,
        "attribution": "unknown", "author_role": None,
        "event_time": None, "observation_time": None,
        "modality": "stated", "polarity": "positive", "status": "active",
        "invalidation_reason": None, "run_id": f"reference:{evidence.id}",
        "metadata": {"reference_witness": True},
    })
    verdict = graph.add_object("evaluation", {
        "subject_id": annotation.id, "subject_type": "semantic_annotation",
        "judgment": "accepted", "rationale": "owner accepted exact selector",
        "evaluator": "owner:test", "metadata": {},
    })
    return annotation, verdict


def test_reference_hardens_through_real_fork_then_deopts_and_demotes_on_drift(tmp_path):
    runtime, settings = _runtime(tmp_path)
    graph = runtime.graph
    witness_texts = [
        "Please send the deck.", "Can you review the draft?",
        "Could you schedule the call?",
    ]
    heldout_texts = ["Please share the notes.", "Can you confirm the date?"]
    witnesses = [_evidence(graph, f"w{i}", text) for i, text in enumerate(witness_texts)]
    heldout = [_evidence(graph, f"h{i}", text) for i, text in enumerate(heldout_texts)]
    counter = _evidence(graph, "counter", "Thanks for sharing the update.")
    for evidence, text in [*zip(witnesses, witness_texts), *zip(heldout, heldout_texts)]:
        _accepted_reference(graph, evidence, text)
    runtime.run_until_idle()

    synthesis = synthesize_request_procedure_fn(
        graph,
        witness_evidence_ids=[row.id for row in witnesses],
        held_out_evidence_ids=[row.id for row in heldout],
        counterexample_evidence_ids=[counter.id],
        created_by="owner:test",
    )
    candidate_id = synthesis["candidate_id"]
    evaluation = evaluate_request_procedure_fn(runtime, candidate_id)
    assert evaluation["ok"] is True
    assert evaluation["fork_run_id"] != runtime.run_id
    assert evaluation["promote_marker_event_id"]
    evaluation_obj = graph.get_object(evaluation["evaluation_id"])
    assert evaluation_obj.data["held_out_parity_milli"] == 1_000
    assert evaluation_obj.data["false_admissions"] == 0
    assert evaluation_obj.data["reference_calls"] == 0

    with pytest.raises(ValueError):
        promote_request_procedure_fn(
            graph, candidate_id, evaluation["evaluation_id"], approver=""
        )
    promoted = promote_request_procedure_fn(
        graph, candidate_id, evaluation["evaluation_id"], approver="owner:test"
    )
    assert promoted["ok"] is True

    update_extraction_profile_fn(
        graph,
        default_facets=["relation_mention"],
        extractor_by_facet={"relation_mention": "semantic.llm@0.1.0"},
        created_by="owner:test",
        rationale="dynamic reference with guarded deterministic procedure",
    )
    provider = RequestReferenceProvider()
    set_llm_provider(provider, ResolvedLLMProvider(
        provider="anthropic", source="setting", api_key_env="ANTHROPIC_API_KEY",
        model="fixture-reference",
    ))

    admitted = _evidence(graph, "new", "Please send the revised deck.")
    result = run_profile_extraction(
        graph, admitted, settings=settings,
        requested_facets=("relation_mention",),
    )
    assert result[0]["run"].data["extractor_id"] == "semantic.request_rule"
    assert provider.calls == 0

    # Three novel shape fingerprints deopt to the exact dynamic reference;
    # the third automatically demotes the procedure.
    for index in range(3):
        drift = _evidence(
            graph, f"drift-{index}", f"Please send drift {index}.",
            shape_version=f"gmail.message@{index + 2}",
        )
        fallback = run_profile_extraction(
            graph, drift, settings=settings,
            requested_facets=("relation_mention",),
        )
        assert fallback[0]["run"].data["extractor_id"] == "semantic.llm"
    deopts = graph.objects(type="procedure_deoptimization")
    assert len(deopts) == 3
    assert all(row.data["fallback_status"] == "completed" for row in deopts)
    assert all(row.data["fallback_run_ids"] for row in deopts)
    assert provider.calls == 3
    assert graph.get_object(candidate_id).data["status"] == "demoted"

    after_demote = _evidence(graph, "after-demote", "Please send one more.")
    routed = run_profile_extraction(
        graph, after_demote, settings=settings,
        requested_facets=("relation_mention",),
    )
    assert routed[0]["run"].data["extractor_id"] == "semantic.llm"
    assert provider.calls == 4


def test_false_admission_on_counterexample_blocks_procedure_promotion(tmp_path):
    runtime, _settings = _runtime(tmp_path)
    graph = runtime.graph
    texts = [
        "Please send the deck.", "Can you review the draft?",
        "Could you schedule the call?", "Please share the notes.",
        "Can you confirm the date?",
    ]
    rows = [_evidence(graph, f"p{i}", text) for i, text in enumerate(texts)]
    for evidence, text in zip(rows, texts):
        _accepted_reference(graph, evidence, text)
    # Same admitted shape and a lexical cue, but the reviewed reference emitted
    # nothing. This is the dangerous loose-guard direction from ADR 0029.
    counter = _evidence(graph, "false-admit", "Please ignore this automated footer.")
    runtime.run_until_idle()
    candidate = synthesize_request_procedure_fn(
        graph,
        witness_evidence_ids=[row.id for row in rows[:3]],
        held_out_evidence_ids=[row.id for row in rows[3:]],
        counterexample_evidence_ids=[counter.id],
        created_by="owner:test",
    )
    evaluated = evaluate_request_procedure_fn(runtime, candidate["candidate_id"])
    assert evaluated["verdict"] == "fail"
    audit = graph.get_object(evaluated["evaluation_id"])
    assert audit.data["false_admissions"] == 1
    assert graph.get_object(candidate["candidate_id"]).data["status"] == "demoted"
    with pytest.raises(ValueError):
        promote_request_procedure_fn(
            graph, candidate["candidate_id"], evaluated["evaluation_id"],
            approver="owner:test",
        )
