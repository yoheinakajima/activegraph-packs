"""The action-class policy dimension at the gateway (ADR 0016, runtime v1.9).

One test per rule:

  * the action-class dimension grants automation only within R0–R2 under
    the runtime's raised ceiling (and can only ADD automation — the
    legacy risk dimension keeps behaving exactly as configured);
  * R3 requires approval and R4 routes to the governance gate at every
    ceiling;
  * a missing action_class fails closed on the class path;
  * a stricter per-capability ceiling (settings.capability_action_ceilings)
    lowers, and can never raise, the instance ceiling;
  * no cross-inference between risk_class and action_class, in either
    direction;
  * MCP-discovered tools default to R3 (presumed outward) and gain a
    lower class only through an explicit operator override;
  * approvals/denials/pending displays carry the class and the
    ceiling-path decline reason;
  * registration-time drift between declared and registered action_class
    is refused (Q8 chain);
  * every first-party capability's declared class matches the review
    inventory.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import pytest
from pydantic import BaseModel, Field

from activegraph import Graph, Runtime

from packs.core import pack as core_pack
from packs.tool_gateway import pack as tg_pack, ToolGatewaySettings
from packs.tool_gateway.behaviors import evaluate_call_policy
from packs.tool_gateway.catalog import catalog_entries
from packs.tool_gateway.gateway import decide_policy, decide_policy_detail
from packs.tool_gateway.llm_tools import as_llm_tool
from packs.tool_gateway.registration_check import (
    arm_registration_enforcement,
    disarm_registration_enforcement,
)
from packs.tool_gateway.tools import (
    approve_capability_fn,
    clear_local_registry,
    deny_capability_fn,
    pending_approvals_fn,
    register_local_capability,
)


class EchoInput(BaseModel):
    text: str = Field(description="Text to echo.")


class _Ctx:
    behavior_name = "test_behavior"
    frame = None


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_local_registry()
    disarm_registration_enforcement()
    yield
    clear_local_registry()
    disarm_registration_enforcement()


def _gateway_rt(settings: ToolGatewaySettings) -> Runtime:
    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    rt.load_pack(tg_pack, settings=settings)
    return rt


def _add_call(rt: Runtime, *, provider="svc", capability="do", risk="medium",
              action="", metadata=None) -> str:
    call = rt.graph.add_object("capability_call", {
        "provider_id": "",
        "provider_name": provider,
        "capability_name": capability,
        "input_data": {},
        "risk_class": risk,
        "action_class": action,
        "status": "proposed",
        "proposed_at": "2026-07-09T00:00:00Z",
        "metadata": metadata or {},
    })
    rt.run_until_idle()
    return call.id


def _status(rt: Runtime, call_id: str) -> str:
    return rt.graph.get_object(call_id).data.get("status")


# ------------------------------------------------ the two dimensions


def test_action_dimension_grants_within_raised_ceiling() -> None:
    # Legacy dimension says hold (medium not in ["low"]); the class
    # dimension grants R0 under a raised ceiling — automation ADDED,
    # explicitly attributed, runtime-audited.
    settings = ToolGatewaySettings()  # auto_approve_risk_classes=["low"]
    rt = _gateway_rt(settings)
    rt.set_authority_ceiling("R2", actor="owner", reason="test raise")

    call_id = _add_call(rt, risk="medium", action="R0")
    assert _status(rt, call_id) == "done"  # auto-approved and executed
    [approval] = list(rt.graph.objects(type="capability_approval"))
    assert approval.data["action_class"] == "R0"
    assert approval.data["metadata"]["granted_by"] == "action_authority"
    audit = [e for e in rt.graph.events if e.type == "authority.decision"]
    assert len(audit) == 1
    assert audit[0].payload["decision"] == "auto_approve"
    assert audit[0].payload["ceiling"] == "R2"


def test_ceiling_default_none_grants_nothing() -> None:
    settings = ToolGatewaySettings(auto_approve_risk_classes=[])
    rt = _gateway_rt(settings)
    call_id = _add_call(rt, risk="low", action="R0")
    assert _status(rt, call_id) == "policy_checking"
    [pending] = pending_approvals_fn(rt.graph)
    assert pending["auto_approve_declined_because"]["matched_policy"] == (
        "above_ceiling"
    )


def test_r3_never_auto_via_action_dimension_at_max_ceiling() -> None:
    settings = ToolGatewaySettings(auto_approve_risk_classes=[])
    rt = _gateway_rt(settings)
    rt.set_authority_ceiling("R2", actor="owner", reason="max ceiling")
    call_id = _add_call(rt, provider="telegram", capability="send_message",
                        risk="low", action="R3")
    assert _status(rt, call_id) == "policy_checking"
    [pending] = pending_approvals_fn(rt.graph)
    assert pending["action_class"] == "R3"
    assert pending["auto_approve_declined_because"]["matched_policy"] == (
        "approval_required_r3"
    )


def test_r4_routes_to_governance_gate_and_holds() -> None:
    settings = ToolGatewaySettings(auto_approve_risk_classes=[])
    rt = _gateway_rt(settings)
    rt.set_authority_ceiling("R2", actor="owner", reason="max ceiling")
    call_id = _add_call(rt, provider="mcp", capability="set_exposure",
                        risk="high", action="R4")
    assert _status(rt, call_id) == "policy_checking"
    [pending] = pending_approvals_fn(rt.graph)
    assert pending["auto_approve_declined_because"]["matched_policy"] == (
        "governance_gate_r4"
    )
    [audit] = [e for e in rt.graph.events if e.type == "authority.decision"]
    assert audit.payload["decision"] == "governance_gate"


def test_missing_action_class_fails_closed_on_class_path() -> None:
    settings = ToolGatewaySettings(auto_approve_risk_classes=[])
    rt = _gateway_rt(settings)
    rt.set_authority_ceiling("R2", actor="owner", reason="max ceiling")
    call_id = _add_call(rt, risk="low", action="")
    assert _status(rt, call_id) == "policy_checking"
    [pending] = pending_approvals_fn(rt.graph)
    assert pending["action_class"] == ""
    assert pending["auto_approve_declined_because"]["matched_policy"] == (
        "fail_closed_missing_action_class"
    )
    # A capability with only a risk_class never gains automation from
    # the class path — and no runtime authority audit event is emitted
    # for it (the new path was never engaged).
    assert not [e for e in rt.graph.events if e.type == "authority.decision"]


def test_stricter_capability_ceiling_lowers_never_raises() -> None:
    settings = ToolGatewaySettings(
        auto_approve_risk_classes=[],
        capability_action_ceilings={"svc.do": "none"},
    )
    rt = _gateway_rt(settings)
    rt.set_authority_ceiling("R2", actor="owner", reason="max ceiling")
    call_id = _add_call(rt, risk="low", action="R0")
    assert _status(rt, call_id) == "policy_checking"
    [pending] = pending_approvals_fn(rt.graph)
    assert pending["auto_approve_declined_because"]["matched_policy"] == (
        "stricter_local_policy"
    )
    # And a LOOSER per-capability entry cannot raise a low instance
    # ceiling (pure-function check; the runtime enforces the same).
    detail = decide_policy_detail(
        "low",
        ToolGatewaySettings(auto_approve_risk_classes=[]),
        action_class="R1",
        authority_ceiling="R0",
        capability_ceiling="R2",
    )
    assert detail["decision"] == "hold"
    assert detail["action_authority"]["matched_policy"] == "above_ceiling"


# ------------------------------------------------ no cross-inference


def test_no_cross_inference_in_either_direction() -> None:
    settings = ToolGatewaySettings()  # legacy: auto-approve ["low"]

    # Direction 1: a scary legacy label does not block the class
    # dimension — action R0 under ceiling R0 grants even at risk
    # "critical" (the labels are separate dimensions).
    detail = decide_policy_detail(
        "critical", ToolGatewaySettings(auto_approve_risk_classes=[]),
        action_class="R0", authority_ceiling="R0",
    )
    assert detail["decision"] == "auto_approve"
    assert detail["granted_by"] == "action_authority"

    # Direction 2: an R3 action class does not retract what the legacy
    # dimension explicitly grants (ADR 0016 migration rule 3: existing
    # static approval configuration keeps behaving as before; the class
    # dimension itself never granted anything here).
    detail = decide_policy_detail(
        "low", settings, action_class="R3", authority_ceiling="R2",
    )
    assert detail["decision"] == "auto_approve"
    assert detail["granted_by"] == "legacy_risk"
    assert detail["action_authority"]["decision"] == "require_approval"

    # And the class path NEVER reads the risk label: an undeclared class
    # holds identically whatever the risk label says.
    for risk in ("low", "medium", "high", "critical"):
        detail = decide_policy_detail(
            risk, ToolGatewaySettings(auto_approve_risk_classes=[]),
            action_class="", authority_ceiling="R2",
        )
        assert detail["action_authority"]["matched_policy"] == (
            "fail_closed_missing_action_class"
        )


def test_legacy_two_arg_decide_policy_unchanged() -> None:
    # The exact pre-v0.7 call shape and semantics.
    settings = ToolGatewaySettings(auto_approve_risk_classes=["low", "medium"])
    assert decide_policy("low", settings) == "auto_approve"
    assert decide_policy("medium", settings) == "auto_approve"
    assert decide_policy("high", settings) == "hold"
    assert decide_policy("critical", settings) == "hold"


def test_legacy_calls_produce_no_authority_events_and_same_decisions() -> None:
    # A host that never declares action_class sees decisions identical
    # to before and NO authority.* events anywhere in the log.
    settings = ToolGatewaySettings()  # defaults
    rt = _gateway_rt(settings)
    auto_id = _add_call(rt, risk="low", action="")
    held_id = _add_call(rt, provider="svc2", capability="write",
                        risk="medium", action="")
    assert _status(rt, auto_id) == "done"
    assert _status(rt, held_id) == "policy_checking"
    assert not [e for e in rt.graph.events if e.type.startswith("authority.")]


# ------------------------------------------------ surfaces


def test_approval_and_denial_objects_carry_action_class() -> None:
    settings = ToolGatewaySettings(auto_approve_risk_classes=[])
    rt = _gateway_rt(settings)
    held_a = _add_call(rt, provider="telegram", capability="send_message",
                       risk="low", action="R3")
    held_b = _add_call(rt, provider="whatsapp", capability="send_message",
                       risk="low", action="R3")

    out = approve_capability_fn(rt.graph, held_a, "owner@example.com")
    assert out["ok"]
    approval = rt.graph.get_object(out["approval_id"])
    assert approval.data["action_class"] == "R3"

    out = deny_capability_fn(rt.graph, held_b, "owner@example.com",
                             reason="not now")
    assert out["ok"]
    denial = rt.graph.get_object(out["denial_id"])
    assert denial.data["action_class"] == "R3"


def test_llm_proxy_records_class_reason_and_audits_via_runtime() -> None:
    register_local_capability(
        "mail", "send", lambda text="": {"sent": text},
        input_schema=EchoInput, description="Send mail.",
        risk_class="high", action_class="R3",
    )
    register_local_capability(
        "notes", "read", lambda text="": {"note": text},
        input_schema=EchoInput, description="Read a note.",
        risk_class="high", action_class="R0",
    )
    settings = ToolGatewaySettings(auto_approve_risk_classes=[])
    rt = _gateway_rt(settings)
    rt.set_authority_ceiling("R1", actor="owner", reason="allow reads/derives")

    from packs.tool_gateway.tools import get_capability_spec

    held = as_llm_tool(rt.graph, get_capability_spec("mail.send"),
                       settings=settings, runtime=rt)
    out = held.fn(EchoInput(text="hi"), _Ctx())
    assert out["status"] == "held_for_approval"
    assert out["action_class"] == "R3"
    assert "approval" in out["action_authority_reason"]

    auto = as_llm_tool(rt.graph, get_capability_spec("notes.read"),
                       settings=settings, runtime=rt)
    out = auto.fn(EchoInput(text="q"), _Ctx())
    assert out["status"] == "done"

    audits = [e for e in rt.graph.events if e.type == "authority.decision"]
    assert [e.payload["decision"] for e in audits] == [
        "require_approval", "auto_approve",
    ]


def test_llm_proxy_without_runtime_grants_nothing_via_class_path() -> None:
    register_local_capability(
        "notes", "read", lambda text="": {"note": text},
        input_schema=EchoInput, description="Read a note.",
        risk_class="high", action_class="R0",
    )
    settings = ToolGatewaySettings(auto_approve_risk_classes=[])
    rt = _gateway_rt(settings)
    rt.set_authority_ceiling("R2", actor="owner", reason="ceiling is set...")

    from packs.tool_gateway.tools import get_capability_spec

    # ...but the proxy was built WITHOUT a runtime handle: the class
    # dimension evaluates against "none" and cannot grant.
    tool = as_llm_tool(rt.graph, get_capability_spec("notes.read"),
                       settings=settings)
    out = tool.fn(EchoInput(text="q"), _Ctx())
    assert out["status"] == "held_for_approval"


def test_catalog_carries_and_filters_action_class() -> None:
    register_local_capability(
        "notes", "read", lambda text="": {},
        input_schema=EchoInput, risk_class="low", action_class="R0",
    )
    register_local_capability(
        "legacy", "thing", lambda text="": {},
        input_schema=EchoInput, risk_class="low",
    )
    entries = {e["key"]: e for e in catalog_entries()}
    assert entries["notes.read"]["action_class"] == "R0"
    assert entries["legacy.thing"]["action_class"] == ""
    only_r0 = catalog_entries(action_class="R0")
    assert [e["key"] for e in only_r0] == ["notes.read"]


# ------------------------------------------------ registration drift


def test_registration_refuses_action_class_drift() -> None:
    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    rt.load_pack(tg_pack, settings=ToolGatewaySettings())
    from activegraph.packs import Pack
    from activegraph.packs.manifest import CapabilityDecl

    declaring = Pack(
        name="declarer",
        version="0.1.0",
        capabilities=(
            CapabilityDecl(provider="svc", capability="read",
                           risk_class="low", action_class="R0"),
            CapabilityDecl(provider="svc", capability="legacy_only",
                           risk_class="low"),
        ),
    )
    rt.load_pack(declaring)
    arm_registration_enforcement(rt.graph)

    # Registered class must equal the declared class...
    with pytest.raises(ValueError, match="action_class"):
        register_local_capability("svc", "read", lambda: {},
                                  risk_class="low", action_class="R2")
    # ...including declared-but-omitted and omitted-but-registered.
    with pytest.raises(ValueError, match="action_class"):
        register_local_capability("svc", "read", lambda: {},
                                  risk_class="low")
    with pytest.raises(ValueError, match="action_class"):
        register_local_capability("svc", "legacy_only", lambda: {},
                                  risk_class="low", action_class="R0")
    # Agreement registers cleanly (both declared and legacy-undeclared).
    register_local_capability("svc", "read", lambda: {},
                              risk_class="low", action_class="R0")
    register_local_capability("svc", "legacy_only", lambda: {},
                              risk_class="low")


def test_register_rejects_invalid_action_class_values() -> None:
    for bad in ("R9", "r0", "low", "medium"):
        with pytest.raises(ValueError, match="action_class"):
            register_local_capability("svc", "x", lambda: {},
                                      risk_class="low", action_class=bad)


# ------------------------------------------------ the native inventory


def test_first_party_capability_classes_match_the_review_inventory() -> None:
    """The review bar, executable: every first-party capability's class."""
    inventory = {
        ("tool_gateway", "catalog", "search"): "R0",
        ("tool_gateway", "web", "fetch_url"): "R0",
        ("schedule", "schedule", "create_reminder"): "R3",
        ("telegram", "telegram", "send_message"): "R3",
        ("whatsapp", "whatsapp", "send_message"): "R3",
        ("mcp", "mcp", "set_exposure"): "R4",
        ("evolution", "evolution", "adopt_proposal"): "R4",
        ("evolution", "evolution", "disable_promotion"): "R4",
    }
    import importlib

    declared: dict[tuple, str] = {}
    for pack_name in {p for p, _, _ in inventory}:
        pack = importlib.import_module(f"packs.{pack_name}").pack
        for decl in pack.capabilities:
            declared[(pack_name, decl.provider, decl.capability)] = (
                decl.action_class
            )
    assert declared == inventory


def test_evaluate_call_policy_reads_runtime_ceiling() -> None:
    settings = ToolGatewaySettings(auto_approve_risk_classes=[])
    rt = _gateway_rt(settings)
    rt.set_authority_ceiling("R1", actor="owner", reason="raise")
    policy = evaluate_call_policy(
        capability_key="svc.do", risk_class="high", action_class="R1",
        settings=settings, runtime=rt,
    )
    assert policy["decision"] == "auto_approve"
    assert policy["action_authority"]["ceiling"] == "R1"
    # The audited runtime event carries the same decision.
    [audit] = [e for e in rt.graph.events if e.type == "authority.decision"]
    assert audit.id == policy["action_authority"]["audit_event_id"]


def test_stricter_per_call_manual_requirement_beats_both_grant_dimensions() -> None:
    settings = ToolGatewaySettings(auto_approve_risk_classes=["medium"])
    rt = _gateway_rt(settings)
    rt.set_authority_ceiling("R2", actor="owner", reason="test")
    policy = evaluate_call_policy(
        capability_key="gmail.drafts.create",
        risk_class="medium",
        action_class="R2",
        settings=settings,
        runtime=rt,
        requires_explicit_approval=True,
    )
    assert policy["decision"] == "hold"
    assert policy["granted_by"] == ""
    assert policy["action_authority"]["matched_policy"] == "call_requires_explicit_approval"
