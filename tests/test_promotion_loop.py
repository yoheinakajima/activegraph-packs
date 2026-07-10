"""P6 acceptance: outcome-driven promotion, replay verification, standing scopes.

The promotion loop's contract, one test per rule:

* reliability evidence GENERATES promote/demote proposals for memory
  artifacts; nothing promotes without an explicit approval, and the
  ``memory.promoted`` event carries the exact SCORING_CONTRACT key
  ``(artifact_id, artifact_version)``;
* promoted skill and memory versions earn ``replay.verified`` keyed
  ``(subject_id, subject_version)`` from recorded re-runs — fork-trial
  for skills, recorded admission/retrieval re-checks for memory — and
  the emitters fail LOUDLY on incomplete or ``reference_only`` lineage
  (ADR 0015);
* sustained prediction accuracy earns a standing-scope ``tool_policy``
  through the governed path (versioned thresholds; approval required),
  the gateway's R2 grant requires it, degradation demotes it naming the
  missed predictions, and the ADR 0018 guards hold structurally:
  no backfill, no R3/R4 standing scopes, local policy always wins,
  the predictor reads no score.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from activegraph import Graph, Runtime, TickingClock

from packs.core import pack as core_pack
from packs.eval_outcome import pack as eval_outcome_pack
from packs.eval_outcome.tools import record_terminal_outcome_fn
from packs.memory_gateway import MemoryGatewaySettings, pack as memory_gateway_pack
from packs.memory_gateway.backend import clear_all_backends
from packs.memory_gateway.promotion import (
    MEMORY_PROMOTION_RULES,
    MemoryReplayIncompleteError,
    resolve_memory_promotion_fn,
    verify_memory_replay_fn,
)
from packs.skills import pack as skills_pack
from packs.skills.tools import (
    author_skill_fn,
    invoke_skill_fn,
    promote_skill_fn,
    record_promotion_evidence_fn,
)
from packs.skills.verification import (
    SkillReplayIncompleteError,
    verify_skill_replay_fn,
)
from packs.tool_gateway import ToolGatewaySettings, pack as tool_gateway_pack
from packs.tool_gateway.gateway import decide_policy_detail
from packs.tool_gateway.standing_scopes import (
    STANDING_SCOPE_RULES,
    accuracy_percent,
    demote_tool_policy_fn,
    disable_tool_policy_fn,
    promote_tool_policy_fn,
    promoted_standing_scope_for,
    propose_standing_scope_fn,
)
from packs.tool_gateway.tools import clear_local_registry, register_local_capability


@pytest.fixture(autouse=True)
def _clean():
    clear_all_backends()
    clear_local_registry()
    yield
    clear_all_backends()
    clear_local_registry()


def _runtime(*packs, gateway_settings=None, persist_to=None) -> Runtime:
    rt = Runtime(
        Graph(clock=TickingClock("2026-07-10T00:00:00Z", step_seconds=1)),
        persist_to=persist_to,
    )
    rt.load_pack(core_pack)
    for pack in packs:
        if pack is tool_gateway_pack and gateway_settings is not None:
            rt.load_pack(pack, settings=gateway_settings)
        elif pack is memory_gateway_pack:
            rt.load_pack(pack, settings=MemoryGatewaySettings(
                acceptance_threshold=0.6,
                auto_accept_categories=["preference"],
            ))
        else:
            rt.load_pack(pack)
    return rt


def _admit_memory(rt, text="the user prefers dark mode everywhere"):
    rt.graph.add_object("memory_candidate", {
        "text": text, "confidence": 0.85, "source_ids": [],
        "observation_ids": [], "category": "preference",
        "subject_ref": None, "accepted": False, "evaluation_id": None,
        "frame_id": "frame_p6",
    })
    rt.run_until_idle()
    items = list(rt.graph.objects(type="memory_item"))
    assert items, "admission failed"
    return items[-1]


def _record_outcome(rt, kind, artifact_id, artifact_type, artifact_version="1",
                    rationale="recorded"):
    evaluation = rt.graph.add_object("evaluation", {
        "subject_id": artifact_id,
        "subject_type": artifact_type,
        "judgment": "accepted",
        "rationale": rationale,
        "evaluator": "owner:test",
    })
    record_terminal_outcome_fn(
        rt.graph,
        kind,
        evaluation_id=evaluation.id,
        rationale=rationale,
        actor="owner",
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        artifact_version=artifact_version,
    )
    rt.run_until_idle()


def _record_helped(rt, item, n, start=0):
    for _ in range(n):
        _record_outcome(rt, "outcome.helped", item.id, "memory_item",
                        str(item.data.get("artifact_version", "1")),
                        rationale="it helped")


def _open_proposals(rt, direction=None):
    out = []
    for obj in rt.graph.objects(type="memory_promotion_proposal"):
        if obj.data.get("status") != "proposed":
            continue
        if direction and obj.data.get("direction") != direction:
            continue
        out.append(obj)
    return out


# ------------------------------------------------------- memory promotion


def test_repeated_helped_outcomes_generate_a_promotion_proposal() -> None:
    rt = _runtime(eval_outcome_pack, memory_gateway_pack)
    item = _admit_memory(rt)
    _record_helped(rt, item, 1)
    assert not _open_proposals(rt), "one helped outcome must not propose"
    _record_helped(rt, item, 1, start=1)
    proposals = _open_proposals(rt, "promote")
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.data["helped_outcomes"] >= MEMORY_PROMOTION_RULES[
        "min_helped_outcomes"
    ]
    assert proposal.data["rule_id"] == MEMORY_PROMOTION_RULES["rule_id"]
    assert proposal.data["rule_version"] == MEMORY_PROMOTION_RULES["rule_version"]
    assert proposal.data["evidence_event_ids"], "proposal must carry evidence"
    # Nothing promoted yet: proposals are not promotions.
    assert not [e for e in rt.graph.events if e.type == "memory.promoted"]
    assert rt.graph.get_object(item.id).data["promotion_status"] == "admitted"


def test_approval_promotes_and_emits_the_contract_key() -> None:
    rt = _runtime(eval_outcome_pack, memory_gateway_pack)
    item = _admit_memory(rt)
    _record_helped(rt, item, 2)
    [proposal] = _open_proposals(rt, "promote")

    with pytest.raises(ValueError, match="approver"):
        resolve_memory_promotion_fn(
            rt.graph, proposal.data["proposal_id"], approve=True, approver="  "
        )

    out = resolve_memory_promotion_fn(
        rt.graph, proposal.data["proposal_id"], approve=True,
        approver="user:owner", note="looks right",
    )
    assert out["changed"]
    [promoted] = [e for e in rt.graph.events if e.type == "memory.promoted"]
    assert promoted.payload["artifact_id"] == item.id
    assert promoted.payload["artifact_version"] == "1"
    assert promoted.payload["approver"] == "user:owner"
    assert promoted.payload["evidence_event_ids"]
    assert rt.graph.get_object(item.id).data["promotion_status"] == "promoted"

    # Re-resolution is a no-op; the version key can never change, so a
    # re-promotion of this version could never re-score downstream.
    again = resolve_memory_promotion_fn(
        rt.graph, proposal.data["proposal_id"], approve=True, approver="user:owner"
    )
    assert not again["changed"]


def test_rejection_closes_the_proposal_without_promoting() -> None:
    rt = _runtime(eval_outcome_pack, memory_gateway_pack)
    item = _admit_memory(rt)
    _record_helped(rt, item, 2)
    [proposal] = _open_proposals(rt, "promote")
    out = resolve_memory_promotion_fn(
        rt.graph, proposal.data["proposal_id"], approve=False,
        approver="user:owner", note="not yet",
    )
    assert out["resolution"] == "rejected"
    assert not [e for e in rt.graph.events if e.type == "memory.promoted"]
    assert rt.graph.get_object(item.id).data["promotion_status"] == "admitted"


def test_harmful_outcome_generates_demotion_and_approval_demotes() -> None:
    rt = _runtime(eval_outcome_pack, memory_gateway_pack)
    item = _admit_memory(rt)
    _record_helped(rt, item, 2)
    [proposal] = _open_proposals(rt, "promote")
    resolve_memory_promotion_fn(
        rt.graph, proposal.data["proposal_id"], approve=True, approver="user:owner"
    )

    _record_outcome(rt, "outcome.hurt", item.id, "memory_item",
                    rationale="it misled the reply")
    [demote] = _open_proposals(rt, "demote")
    assert demote.data["reliability_verdict"] in MEMORY_PROMOTION_RULES[
        "demote_on_verdicts"
    ]
    out = resolve_memory_promotion_fn(
        rt.graph, demote.data["proposal_id"], approve=True, approver="user:owner"
    )
    assert out["changed"]
    [demoted] = [e for e in rt.graph.events if e.type == "memory.demoted"]
    assert demoted.payload["artifact_id"] == item.id
    assert rt.graph.get_object(item.id).data["promotion_status"] == "demoted"


# ------------------------------------------------------- replay.verified


def _promote_memory(rt):
    item = _admit_memory(rt)
    _record_helped(rt, item, 2)
    [proposal] = _open_proposals(rt, "promote")
    resolve_memory_promotion_fn(
        rt.graph, proposal.data["proposal_id"], approve=True, approver="user:owner"
    )
    return rt.graph.get_object(item.id)


def test_promoted_memory_earns_replay_verified_with_contract_key() -> None:
    rt = _runtime(eval_outcome_pack, memory_gateway_pack)
    item = _promote_memory(rt)
    out = verify_memory_replay_fn(rt.graph, item.id, runtime=rt)
    assert out["created"]
    [event] = [e for e in rt.graph.events if e.type == "replay.verified"]
    assert event.payload["subject_id"] == item.id
    assert event.payload["subject_version"] == "1"
    assert event.payload["subject_type"] == "memory_item"
    assert event.payload["method"] == "recorded_admission_and_retrieval_recheck"
    # Once per version: re-verification returns the recorded event.
    again = verify_memory_replay_fn(rt.graph, item.id, runtime=rt)
    assert not again["created"]
    assert again["event_id"] == out["event_id"]


def test_unpromoted_memory_cannot_claim_replay_verified() -> None:
    rt = _runtime(eval_outcome_pack, memory_gateway_pack)
    item = _admit_memory(rt)
    with pytest.raises(ValueError, match="promoted"):
        verify_memory_replay_fn(rt.graph, item.id, runtime=rt)


def test_reference_only_lineage_fails_loudly_for_memory() -> None:
    rt = _runtime(eval_outcome_pack, memory_gateway_pack)
    source = rt.graph.add_object("source", {
        "kind": "note", "content": "x", "channel": "test",
        "metadata": {
            "replay_complete": False,
            "acquisition": {"replay_mode": "reference_only"},
        },
    })
    rt.graph.add_object("memory_candidate", {
        "text": "reference only memory", "confidence": 0.9,
        "source_ids": [source.id], "observation_ids": [],
        "category": "preference", "subject_ref": None,
        "accepted": False, "evaluation_id": None, "frame_id": "frame_p6",
    })
    rt.run_until_idle()
    item = list(rt.graph.objects(type="memory_item"))[-1]
    _record_helped(rt, item, 2)
    [proposal] = _open_proposals(rt, "promote")
    resolve_memory_promotion_fn(
        rt.graph, proposal.data["proposal_id"], approve=True, approver="user:owner"
    )
    with pytest.raises(MemoryReplayIncompleteError, match="ADR 0015"):
        verify_memory_replay_fn(rt.graph, item.id, runtime=rt)
    assert not [e for e in rt.graph.events if e.type == "replay.verified"]


def _promoted_skill(rt, *, with_usage=True):
    author_skill_fn(
        rt.graph, "weekly_briefing", "1.0.0",
        description="assemble the weekly briefing",
        source_evidence_refs=["evidence_fixture_1"],
        actor="owner", is_fixture=False,
    )
    skill = list(rt.graph.objects(type="skill"))[-1]
    if with_usage:
        invoke_skill_fn(
            rt.graph, "weekly_briefing", "1.0.0",
            usage_id="usage_p6_1", execution_ref="run_p6_1",
            execution_kind="trial", actor="agent",
        )
    record_promotion_evidence_fn(
        rt.graph, skill.id, kind="trial",
        reference_ids=["usage_p6_1"] if with_usage else ["manual"],
        rationale="trial passed", actor="owner",
    )
    evidence = list(rt.graph.objects(type="skill_promotion_evidence"))[-1]
    promote_skill_fn(
        rt.graph, skill.id, evidence.data["evidence_id"],
        rationale="trial passed", actor="owner",
    )
    return rt.graph.get_object(skill.id)


def test_promoted_skill_earns_replay_verified_via_fork_trial(tmp_path) -> None:
    # fork-trial needs a SQLite-backed runtime (runtime CONTRACT v0.8 #5).
    rt = _runtime(eval_outcome_pack, skills_pack,
                  persist_to=str(tmp_path / "p6_skills.db"))
    skill = _promoted_skill(rt)
    out = verify_skill_replay_fn(rt.graph, rt, skill.id)
    assert out["created"]
    [event] = [e for e in rt.graph.events if e.type == "replay.verified"]
    assert event.payload["subject_id"] == skill.data["skill_id"]
    assert event.payload["subject_version"] == "1.0.0"
    assert event.payload["subject_type"] == "skill_version"
    assert event.payload["method"] == "fork_trial_rerun"
    assert event.payload["recorded_usage_id"] == "usage_p6_1"
    # The fork re-run leaves no replay usage on the REAL graph.
    assert not [
        u for u in rt.graph.objects(type="skill_usage")
        if str(u.data.get("usage_id", "")).startswith("replay::")
    ]
    again = verify_skill_replay_fn(rt.graph, rt, skill.id)
    assert not again["created"]


def test_unpromoted_or_unused_skills_fail_loudly() -> None:
    rt = _runtime(eval_outcome_pack, skills_pack)
    author_skill_fn(
        rt.graph, "unused", "1.0.0", description="d",
        source_evidence_refs=["e"], actor="owner",
    )
    skill = list(rt.graph.objects(type="skill"))[-1]
    with pytest.raises(ValueError, match="promoted"):
        verify_skill_replay_fn(rt.graph, rt, skill.id)


def test_reference_only_lineage_fails_loudly_for_skills(tmp_path) -> None:
    rt = _runtime(eval_outcome_pack, skills_pack,
                  persist_to=str(tmp_path / "p6_skills_ref.db"))
    evidence = rt.graph.add_object("source", {
        "kind": "export", "content": "…", "channel": "importer",
        "metadata": {
            "replay_complete": False,
            "acquisition": {"replay_mode": "reference_only"},
        },
    })
    author_skill_fn(
        rt.graph, "ref_only_skill", "1.0.0", description="d",
        source_evidence_refs=[evidence.id], actor="owner",
    )
    skill = list(rt.graph.objects(type="skill"))[-1]
    invoke_skill_fn(
        rt.graph, "ref_only_skill", "1.0.0",
        usage_id="usage_ref_1", execution_ref="run_ref_1",
        execution_kind="trial", actor="agent",
    )
    record_promotion_evidence_fn(
        rt.graph, skill.id, kind="trial", reference_ids=["usage_ref_1"],
        rationale="trial passed", actor="owner",
    )
    ev = list(rt.graph.objects(type="skill_promotion_evidence"))[-1]
    promote_skill_fn(rt.graph, skill.id, ev.data["evidence_id"],
                     rationale="ok", actor="owner")
    with pytest.raises(SkillReplayIncompleteError, match="ADR 0015"):
        verify_skill_replay_fn(rt.graph, rt, skill.id)


# ------------------------------------------------------- standing scopes


def _prediction_history(rt, n_matched, n_missed, capability="notes.hold_slot"):
    """Deterministic prediction/decision pairs: predictions strictly
    precede decisions in the log (the no-backfill shape)."""

    pairs = []
    for index in range(n_matched + n_missed):
        prediction = rt.graph.add_object("approval_prediction", {
            "prediction_id": f"pred_{capability}_{index}",
            "scope_key": f"R2|{capability}",
            "predicted_verdict": "approve",
            "confidence_percent": 92,
        })
        decided = rt.graph.add_object("decision_fact", {
            "decision_id": f"dec_{capability}_{index}",
            "verdict": "approved" if index < n_matched else "rejected",
        })
        pairs.append({
            "prediction_ref": prediction.id,
            "decided_ref": decided.id,
            "predicted_verdict": "approve",
            "actual_verdict": "approved" if index < n_matched else "rejected",
        })
    return pairs


def test_standing_scope_earned_from_deterministic_history() -> None:
    rt = _runtime(eval_outcome_pack, tool_gateway_pack)
    pairs = _prediction_history(rt, 9, 1)
    assert accuracy_percent(pairs) == 90
    out = propose_standing_scope_fn(
        rt.graph, capability_key="notes.hold_slot", action_class="R2",
        prediction_pairs=pairs, proposed_by="babyagi.reflection",
    )
    assert out["created"]
    policy = out["policy"]
    assert policy.data["status"] == "candidate"
    assert policy.data["evidence"]["prediction_count"] == 10
    assert policy.data["evidence"]["accuracy_percent"] == 90
    assert policy.data["evidence"]["thresholds"]["rule_version"] == (
        STANDING_SCOPE_RULES["rule_version"]
    )
    [proposed] = [e for e in rt.graph.events if e.type == "tool_policy.proposed"]
    assert proposed.payload["policy_version"] == 1

    # Candidate grants nothing until an approver promotes it.
    assert promoted_standing_scope_for(rt.graph, "notes.hold_slot", "R2") is None
    promo = promote_tool_policy_fn(
        rt.graph, policy.data["policy_id"], "user:owner", note="handle these"
    )
    assert promo["changed"]
    [promoted] = [e for e in rt.graph.events if e.type == "tool_policy.promoted"]
    assert promoted.payload["policy_id"] == policy.data["policy_id"]
    assert promoted.payload["policy_version"] == 1
    assert promoted.payload["approver"] == "user:owner"
    assert promoted.payload["prediction_count"] == 10
    scope = promoted_standing_scope_for(rt.graph, "notes.hold_slot", "R2")
    assert scope is not None and scope["accuracy_percent"] == 90


def test_thresholds_are_enforced_below_the_versioned_bar() -> None:
    rt = _runtime(eval_outcome_pack, tool_gateway_pack)
    # 8/10 = 80% accuracy: below the 90% bar.
    pairs = _prediction_history(rt, 8, 2, capability="a.b")
    with pytest.raises(ValueError, match="accuracy"):
        propose_standing_scope_fn(
            rt.graph, capability_key="a.b", action_class="R2",
            prediction_pairs=pairs, proposed_by="test",
        )
    # 7 pairs: below the count bar even at 100%.
    pairs = _prediction_history(rt, 7, 0, capability="c.d")
    with pytest.raises(ValueError, match="8"):
        propose_standing_scope_fn(
            rt.graph, capability_key="c.d", action_class="R2",
            prediction_pairs=pairs, proposed_by="test",
        )


def test_r3_and_r4_scopes_are_structurally_impossible() -> None:
    rt = _runtime(eval_outcome_pack, tool_gateway_pack)
    pairs = _prediction_history(rt, 10, 0, capability="mail.send")
    for cls in ("R3", "R4"):
        with pytest.raises(ValueError, match="never become standing"):
            propose_standing_scope_fn(
                rt.graph, capability_key="mail.send", action_class=cls,
                prediction_pairs=pairs, proposed_by="test",
            )
    # And a forged scope record cannot leak automation into R3: the R3
    # branch decides before any scope is consulted.
    detail = decide_policy_detail(
        "low", ToolGatewaySettings(auto_approve_risk_classes=[]),
        action_class="R3", authority_ceiling="R2",
        standing_scope={"policy_id": "forged", "policy_version": 1},
    )
    assert detail["decision"] == "hold"
    assert detail["action_authority"]["matched_policy"] == "approval_required_r3"


def test_backfilled_predictions_can_never_earn_automation() -> None:
    rt = _runtime(eval_outcome_pack, tool_gateway_pack)
    pairs = _prediction_history(rt, 9, 1, capability="e.f")
    # Swap one pair's refs: the "prediction" now points at the later
    # decision object — recorded after the verdict.
    pairs[0]["prediction_ref"], pairs[0]["decided_ref"] = (
        pairs[0]["decided_ref"], pairs[0]["prediction_ref"],
    )
    with pytest.raises(ValueError, match="backfilled|precede"):
        propose_standing_scope_fn(
            rt.graph, capability_key="e.f", action_class="R2",
            prediction_pairs=pairs, proposed_by="test",
        )
    # Unresolvable refs fail closed too.
    pairs = _prediction_history(rt, 9, 1, capability="g.h")
    pairs[3]["prediction_ref"] = "evt_never_recorded"
    with pytest.raises(ValueError, match="resolve"):
        propose_standing_scope_fn(
            rt.graph, capability_key="g.h", action_class="R2",
            prediction_pairs=pairs, proposed_by="test",
        )


def test_gateway_r2_requires_promoted_standing_scope() -> None:
    settings = ToolGatewaySettings(auto_approve_risk_classes=[])
    rt = _runtime(eval_outcome_pack, tool_gateway_pack, gateway_settings=settings)
    rt.set_authority_ceiling("R2", actor="owner", reason="L3 mapping")

    def add_r2_call():
        call = rt.graph.add_object("capability_call", {
            "provider_id": "", "provider_name": "notes",
            "capability_name": "hold_slot", "input_data": {},
            "risk_class": "medium", "action_class": "R2",
            "status": "proposed", "proposed_at": "2026-07-10T00:00:00Z",
        })
        rt.run_until_idle()
        return rt.graph.get_object(call.id)

    # Within the ceiling but no promoted scope: held, precisely named.
    held = add_r2_call()
    assert held.data["status"] == "policy_checking"
    authority = held.data["metadata"]["action_authority"]
    assert authority["matched_policy"] == "r2_requires_promoted_standing_scope"
    assert authority["standing_scope"] is None

    # Earn and promote the scope, then the same call auto-approves and
    # the approval carries the scope provenance.
    pairs = _prediction_history(rt, 9, 1)
    out = propose_standing_scope_fn(
        rt.graph, capability_key="notes.hold_slot", action_class="R2",
        prediction_pairs=pairs, proposed_by="babyagi.reflection",
    )
    promote_tool_policy_fn(rt.graph, out["policy"].data["policy_id"], "user:owner")
    auto = add_r2_call()
    assert auto.data["status"] == "done"
    approvals = [
        a for a in rt.graph.objects(type="capability_approval")
        if a.data.get("call_id") == auto.id
    ]
    scope_meta = approvals[0].data["metadata"]["action_authority"]["standing_scope"]
    assert scope_meta["policy_id"] == "tool_policy_R2|notes.hold_slot"
    assert scope_meta["prediction_count"] == 10
    assert scope_meta["accuracy_percent"] == 90
    assert approvals[0].data["metadata"]["granted_by"] == "action_authority"


def test_local_policy_always_keeps_a_scope_manual() -> None:
    settings = ToolGatewaySettings(
        auto_approve_risk_classes=[],
        capability_action_ceilings={"notes.hold_slot": "R1"},
    )
    rt = _runtime(eval_outcome_pack, tool_gateway_pack, gateway_settings=settings)
    rt.set_authority_ceiling("R2", actor="owner", reason="L3 mapping")
    pairs = _prediction_history(rt, 9, 1)
    out = propose_standing_scope_fn(
        rt.graph, capability_key="notes.hold_slot", action_class="R2",
        prediction_pairs=pairs, proposed_by="test",
    )
    promote_tool_policy_fn(rt.graph, out["policy"].data["policy_id"], "user:owner")

    call = rt.graph.add_object("capability_call", {
        "provider_id": "", "provider_name": "notes",
        "capability_name": "hold_slot", "input_data": {},
        "risk_class": "medium", "action_class": "R2",
        "status": "proposed", "proposed_at": "2026-07-10T00:00:00Z",
    })
    rt.run_until_idle()
    held = rt.graph.get_object(call.id)
    assert held.data["status"] == "policy_checking"
    assert held.data["metadata"]["action_authority"]["matched_policy"] == (
        "stricter_local_policy"
    )


def test_degradation_demotes_naming_the_missed_predictions() -> None:
    settings = ToolGatewaySettings(auto_approve_risk_classes=[])
    rt = _runtime(eval_outcome_pack, tool_gateway_pack, gateway_settings=settings)
    rt.set_authority_ceiling("R2", actor="owner", reason="L3 mapping")
    pairs = _prediction_history(rt, 9, 1)
    out = propose_standing_scope_fn(
        rt.graph, capability_key="notes.hold_slot", action_class="R2",
        prediction_pairs=pairs, proposed_by="test",
    )
    policy_id = out["policy"].data["policy_id"]
    promote_tool_policy_fn(rt.graph, policy_id, "user:owner")

    # The fixture turns wrong: three fresh misses drop trailing accuracy
    # to 9/13 = 69%.
    missed = _prediction_history(rt, 0, 3, capability="notes.hold_slot")
    all_pairs = pairs + missed
    observed = accuracy_percent(all_pairs)
    assert observed < STANDING_SCOPE_RULES["demote_below_accuracy_percent"]
    demoted = demote_tool_policy_fn(
        rt.graph, policy_id,
        missed_prediction_refs=[p["prediction_ref"] for p in missed],
        observed_accuracy_percent=observed,
        actor="babyagi.reflection",
    )
    assert demoted["changed"]
    [event] = [e for e in rt.graph.events if e.type == "tool_policy.demoted"]
    assert len(event.payload["missed_prediction_refs"]) == 3
    assert event.payload["observed_accuracy_percent"] == observed

    # Reversible and immediately effective: the next R2 call holds again.
    assert promoted_standing_scope_for(rt.graph, "notes.hold_slot", "R2") is None
    call = rt.graph.add_object("capability_call", {
        "provider_id": "", "provider_name": "notes",
        "capability_name": "hold_slot", "input_data": {},
        "risk_class": "medium", "action_class": "R2",
        "status": "proposed", "proposed_at": "2026-07-10T00:00:00Z",
    })
    rt.run_until_idle()
    assert rt.graph.get_object(call.id).data["status"] == "policy_checking"

    # Re-proposal after demotion starts the next policy version through
    # the same governed path — never a silent re-grant.
    fresh = _prediction_history(rt, 10, 0, capability="notes.hold_slot")
    out = propose_standing_scope_fn(
        rt.graph, capability_key="notes.hold_slot", action_class="R2",
        prediction_pairs=fresh, proposed_by="test",
    )
    assert out["policy"].data["policy_version"] == 2
    assert out["policy"].data["status"] == "candidate"


def test_reliability_guard_demotes_but_never_repromotes() -> None:
    settings = ToolGatewaySettings(auto_approve_risk_classes=[])
    rt = _runtime(eval_outcome_pack, tool_gateway_pack, gateway_settings=settings)
    pairs = _prediction_history(rt, 9, 1)
    out = propose_standing_scope_fn(
        rt.graph, capability_key="notes.hold_slot", action_class="R2",
        prediction_pairs=pairs, proposed_by="test",
    )
    policy = out["policy"]
    promote_tool_policy_fn(rt.graph, policy.data["policy_id"], "user:owner")

    _record_outcome(rt, "outcome.hurt", policy.id, "tool_policy",
                    str(policy.data["policy_version"]),
                    rationale="an auto-approved call misfired")
    assert rt.graph.get_object(policy.id).data["status"] == "demoted"

    # Recovery evidence does NOT silently restore an automation grant.
    _record_outcome(rt, "outcome.helped", policy.id, "tool_policy",
                    str(policy.data["policy_version"]),
                    rationale="fine now")
    assert rt.graph.get_object(policy.id).data["status"] == "demoted"


def test_owner_disable_is_final_until_a_fresh_proposal() -> None:
    rt = _runtime(eval_outcome_pack, tool_gateway_pack)
    pairs = _prediction_history(rt, 9, 1)
    out = propose_standing_scope_fn(
        rt.graph, capability_key="notes.hold_slot", action_class="R2",
        prediction_pairs=pairs, proposed_by="test",
    )
    policy_id = out["policy"].data["policy_id"]
    promote_tool_policy_fn(rt.graph, policy_id, "user:owner")
    disable_tool_policy_fn(rt.graph, policy_id, actor="user:owner",
                           reason="not comfortable yet")
    with pytest.raises(ValueError, match="fresh proposal"):
        promote_tool_policy_fn(rt.graph, policy_id, "user:owner")


def test_promotion_machinery_reads_no_score_and_no_product_state() -> None:
    """The predictor/promoter boundary: agreement with verdicts, not score."""

    for module in ("tool_gateway/standing_scopes.py",
                   "memory_gateway/promotion.py",
                   "skills/verification.py"):
        source = (Path(__file__).parents[1] / "packs" / module).read_text()
        for forbidden in ("total_score", "project_score", "babyagi"):
            assert forbidden not in source, f"{module} must not read {forbidden}"
