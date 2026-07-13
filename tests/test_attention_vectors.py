"""ADR 0028 v1: learned importance and trust remain distinct and explainable."""

from activegraph import Graph, Runtime

from packs.attention import pack as attention_pack
from packs.attention.tools import (
    get_importance_fn,
    get_source_trust_fn,
    rank_importance_fn,
    record_attention_observation_fn,
)
from packs.eval_outcome.tools import _emit_event


def _runtime() -> Runtime:
    runtime = Runtime(Graph())
    runtime.load_pack(attention_pack)
    runtime.run_until_idle()
    return runtime


def _observe(runtime: Runtime, observation_id: str, subject_ref: str, signal: str, **kw):
    record_attention_observation_fn(
        runtime.graph,
        observation_id=observation_id,
        subject_ref=subject_ref,
        signal_type=signal,
        **kw,
    )
    runtime.run_until_idle()


def test_importance_is_context_scoped_explainable_and_not_llm_asserted():
    runtime = _runtime()
    _observe(runtime, "o1", "message:1", "opened", context_key="inbox")
    _observe(runtime, "o2", "message:1", "active_dwell", context_key="inbox", strength_milli=500)
    inbox = get_importance_fn(runtime.graph, "message:1", context_key="inbox")
    assert inbox["score_milli"] > 500
    assert inbox["features"] == {"engagement": 270}
    assert inbox["evidence_refs"] == ["o1", "o2"]
    assert get_importance_fn(runtime.graph, "message:1", context_key="project:x")["priority_band"] == "unranked"

    _observe(runtime, "o3", "message:1", "llm_judgment", context_key="inbox")
    after_model = get_importance_fn(runtime.graph, "message:1", context_key="inbox")
    assert after_model["score_milli"] == inbox["score_milli"]
    assert after_model["metadata"]["llm_direct_weight"] == 0


def test_explicit_signal_requires_explicit_owner_act_and_absence_requires_opportunity():
    runtime = _runtime()
    _observe(runtime, "named-only", "task:1", "explicit_important")
    assert get_importance_fn(runtime.graph, "task:1")["priority_band"] == "unranked"

    _observe(runtime, "owner-mark", "task:1", "explicit_important", explicit=True)
    marked = get_importance_fn(runtime.graph, "task:1")
    assert marked["priority_band"] == "high"
    assert marked["features"]["explicit"] == 1_000

    try:
        record_attention_observation_fn(
            runtime.graph,
            observation_id="bad-absence",
            subject_ref="task:1",
            signal_type="nonresponse_window",
        )
    except ValueError as exc:
        assert "opportunity_id" in str(exc)
    else:
        raise AssertionError("uncensored absence must fail closed")


def test_ranking_reserves_deterministic_exploration_and_exposes_reason():
    runtime = _runtime()
    _observe(runtime, "important", "task:important", "completed")
    decision = rank_importance_fn(
        runtime.graph,
        ["task:unknown-b", "task:important", "task:unknown-a"],
        limit=2,
        exploration_slots=1,
    )
    assert [row["selection_reason"] for row in decision["items"]] == [
        "importance", "exploration",
    ]
    assert decision["items"][0]["subject_ref"] == "task:important"
    assert decision == rank_importance_fn(
        runtime.graph,
        ["task:unknown-b", "task:important", "task:unknown-a"],
        limit=2,
        exploration_slots=1,
    )


def test_source_trust_is_domain_scoped_and_only_canonical_outcomes_count():
    runtime = _runtime()
    source_context = {
        "trust_sources": [{
            "source_ref": "surface:crm",
            "source_kind": "connector_surface",
            "domain": "investor_membership",
            "query_scope": "current_lps",
        }]
    }
    _emit_event(runtime.graph, "outcome.helped", {
        "artifact_id": "claim:1", "artifact_type": "memory_claim",
        "source_context": source_context,
    }, "owner:test")
    runtime.run_until_idle()
    supported = get_source_trust_fn(
        runtime.graph, "surface:crm",
        domain="investor_membership", query_scope="current_lps",
    )
    assert supported["verdict"] == "supported"
    assert supported["features"] == {"helped": 1_000}
    assert get_source_trust_fn(runtime.graph, "surface:crm", domain="biography")["verdict"] == "unproven"

    _emit_event(runtime.graph, "outcome.contradicted", {
        "artifact_id": "claim:2", "artifact_type": "memory_claim",
        "source_context": source_context,
    }, "owner:test")
    runtime.run_until_idle()
    challenged = get_source_trust_fn(
        runtime.graph, "surface:crm",
        domain="investor_membership", query_scope="current_lps",
    )
    assert challenged["verdict"] == "weak"
    assert challenged["score_milli"] == 500
    assert len(challenged["evidence_refs"]) == 2


def test_source_self_description_without_outcome_never_creates_trust():
    runtime = _runtime()
    runtime.graph.add_object("attention_observation", {
        "observation_id": "source-says-trusted",
        "subject_ref": "surface:web",
        "subject_kind": "connector_surface",
        "signal_type": "llm_judgment",
        "metadata": {"source_claimed_trust": 1_000},
    })
    runtime.run_until_idle()
    assert not runtime.graph.objects(type="source_trust_vector")
