"""Plan purpose/series identity (Phase 5c closure — Gate A/B).

The 2026-07-16 owner run preserved a store where the Gmail initial backfill
(v1, fulfilled) and the consented sent-mail comprehension (v2, approved)
shared ONE plan series because ``plan_series_id`` omitted purpose. Head
resolution then had to pick a single current plan per surface, so two
independent owner-approved intents could never both be discoverable work,
and proposal ordering grew hidden constraints (a comprehension proposal
while the backfill was approved raised).

These tests pin the fix: purpose participates in series identity for
non-default purposes (the default keeps the historical hash so stored ids
stay valid), execution gates anchor to the plan's OWN series head, and the
exact preserved-store shape stays discoverable and executable.
"""

from __future__ import annotations

import pytest

from activegraph import Graph, Runtime

from packs.connector_control import pack as connector_control_pack
from packs.connector_control.plans import (
    approve_ingestion_plan_fn,
    begin_deferred_plan_execution_fn,
    bind_plan_execution_fn,
    current_plan_for_surface_fn,
    edit_ingestion_plan_fn,
    pending_deferred_plan_executions_fn,
    plan_series_id,
    propose_ingestion_plan_fn,
    register_deferred_plan_execution,
    settle_plan_for_run_fn,
    unregister_deferred_plan_execution,
)


SURFACE = "mailbox:owner"
SERVICE = "fixture_mail"


@pytest.fixture
def runtime():
    rt = Runtime(Graph())
    rt.load_pack(connector_control_pack)
    rt.run_until_idle()
    register_deferred_plan_execution(
        SERVICE,
        prepare=lambda graph, plan: {"plan": plan.data.get("plan_identity")},
        perform=lambda payload: {"ok": True},
        commit=lambda graph, plan, payload, outcome: {"ok": True, "run_id": None},
    )
    yield rt
    unregister_deferred_plan_execution(SERVICE)


def _propose(graph, purpose: str = "initial_backfill", **overrides):
    payload = {
        "source_surface_id": SURFACE,
        "service": SERVICE,
        "account_ref": "owner@example.com",
        "family": "conversation",
        "window": {"kind": "recent_days", "days": 30, "estimated_items": 40},
        "derivation": {
            "basis": "service_default",
            "summary": "fixture derivation",
            "measurements": {},
            "provenance": [],
        },
        "surfaces": [{
            "surface_ref": "inbox",
            "label": "inbox",
            "included": True,
            "expectation": {"estimated_richness": "unmeasured"},
        }],
        "caps": {"max_items": 100, "max_pages": 4},
        "interpretation_stages": ["fixture.mapper@0.1.0"],
        "proposed_by": "fixture.plan_proposer",
        "purpose": purpose,
    }
    payload.update(overrides)
    return propose_ingestion_plan_fn(graph, **payload)


def _pending_identities(graph) -> set[str]:
    return {
        row["plan_identity"]
        for row in pending_deferred_plan_executions_fn(graph)
    }


def test_default_purpose_series_id_keeps_the_historical_hash():
    # Stored series ids from every existing store must remain valid: the
    # default purpose hashes exactly as the four-part historical identity.
    legacy = plan_series_id(SURFACE, SERVICE, "owner@example.com", "conversation")
    default = plan_series_id(
        SURFACE, SERVICE, "owner@example.com", "conversation",
        purpose="initial_backfill",
    )
    comprehension = plan_series_id(
        SURFACE, SERVICE, "owner@example.com", "conversation",
        purpose="comprehension",
    )
    assert default == legacy
    assert comprehension != legacy


def test_comprehension_proposal_coexists_with_an_approved_backfill(runtime):
    graph = runtime.graph
    backfill = _propose(graph)["plan"]
    approve_ingestion_plan_fn(
        graph, plan_ref=backfill.data["plan_identity"], approved_by="owner",
    )
    # The one-consent flow approves both intents up front; proposing the
    # study must not depend on the backfill having finished.
    study = _propose(graph, purpose="comprehension")["plan"]
    assert study.data["purpose"] == "comprehension"
    assert study.data["plan_series"] != backfill.data["plan_series"]
    assert study.data["version"] == 1
    assert study.data["supersedes"] is None
    # The backfill was neither superseded nor re-versioned by the proposal.
    live_backfill = graph.get_object(backfill.id)
    assert live_backfill.data["status"] == "approved"


def test_comprehension_proposal_coexists_with_an_executing_backfill(runtime):
    graph = runtime.graph
    backfill = _propose(graph)["plan"]
    approve_ingestion_plan_fn(
        graph, plan_ref=backfill.data["plan_identity"], approved_by="owner",
    )
    bind_plan_execution_fn(
        graph,
        plan_ref=backfill.data["plan_identity"],
        domain_run_id="fixture_run#1",
        source_surface_id=SURFACE,
    )
    study = _propose(graph, purpose="comprehension")["plan"]
    assert study.data["version"] == 1
    assert graph.get_object(backfill.id).data["status"] == "executing"


def test_two_approved_purposes_are_both_discoverable_work(runtime):
    graph = runtime.graph
    backfill = _propose(graph)["plan"]
    approve_ingestion_plan_fn(
        graph, plan_ref=backfill.data["plan_identity"], approved_by="owner",
    )
    study = _propose(graph, purpose="comprehension")["plan"]
    approve_ingestion_plan_fn(
        graph, plan_ref=study.data["plan_identity"], approved_by="owner",
    )
    pending = _pending_identities(graph)
    assert backfill.data["plan_identity"] in pending
    assert study.data["plan_identity"] in pending
    # Both begin cleanly — neither is "not current" because of the other.
    for plan in (backfill, study):
        begun = begin_deferred_plan_execution_fn(
            graph, plan_ref=plan.data["plan_identity"],
        )
        assert begun["ok"], begun


def test_backfill_reproposal_never_evaporates_an_approved_study(runtime):
    graph = runtime.graph
    backfill = _propose(graph)["plan"]
    approve_ingestion_plan_fn(
        graph, plan_ref=backfill.data["plan_identity"], approved_by="owner",
    )
    bind_plan_execution_fn(
        graph,
        plan_ref=backfill.data["plan_identity"],
        domain_run_id="fixture_run#1",
        source_surface_id=SURFACE,
    )
    settle_plan_for_run_fn(graph, domain_run_id="fixture_run#1", state="succeeded")

    study = _propose(graph, purpose="comprehension")["plan"]
    approve_ingestion_plan_fn(
        graph, plan_ref=study.data["plan_identity"], approved_by="owner",
    )
    assert study.data["plan_identity"] in _pending_identities(graph)

    # An explicit refresh re-proposes the backfill (its own series, next
    # version). The approved study must stay discoverable and executable —
    # an owner-approved contract cannot silently stop being current.
    refreshed = _propose(
        graph,
        window={"kind": "recent_days", "days": 7, "estimated_items": 10},
    )["plan"]
    assert refreshed.data["purpose"] == "initial_backfill"
    pending = _pending_identities(graph)
    assert study.data["plan_identity"] in pending
    begun = begin_deferred_plan_execution_fn(
        graph, plan_ref=study.data["plan_identity"],
    )
    assert begun["ok"], begun


def test_preserved_store_shape_stays_discoverable_and_executable(runtime):
    """The exact 2026-07-16 owner-store shape: one legacy series holding the
    fulfilled backfill as v1 and the approved comprehension as v2 (minted
    before purpose joined series identity). Replayed stores must keep that
    approved study discoverable and executable."""
    graph = runtime.graph
    legacy_series = plan_series_id(
        SURFACE, SERVICE, "owner@example.com", "conversation",
    )
    caps = {
        "max_items": 250, "max_pages": 10, "page_size": 25,
        "policy_id": "connector-operational@0.3.0", "policy_version": 3,
        "ceiling_items": 250, "ceiling_pages": 10,
    }
    graph.add_object("connector_ingestion_plan", {
        "plan_identity": "ingestion_plan_legacy_v1",
        "plan_series": legacy_series,
        "version": 1,
        "status": "fulfilled",
        "source_surface_id": SURFACE,
        "service": SERVICE,
        "account_ref": "owner@example.com",
        "family": "conversation",
        "purpose": "initial_backfill",
        "window": {"kind": "recent_days", "days": 30, "estimated_items": 250},
        "derivation": {
            "basis": "service_default", "summary": "preserved-store shape",
            "measurements": {}, "provenance": [],
        },
        "surfaces": [],
        "caps": caps,
        "interpretation_stages": [],
        "predicted_verdict": "approved_as_proposed",
        "predicted_confidence_percent": 50,
        "prediction_basis": {},
        "verdict": "approved_as_proposed",
        "proposed_by": "fixture.plan_proposer",
        "domain_run_id": "fixture_run#419",
    })
    graph.add_object("connector_ingestion_plan", {
        "plan_identity": "ingestion_plan_legacy_v2_comprehension",
        "plan_series": legacy_series,
        "version": 2,
        "status": "approved",
        "source_surface_id": SURFACE,
        "service": SERVICE,
        "account_ref": "owner@example.com",
        "family": "conversation",
        "purpose": "comprehension",
        "window": {"kind": "recent_items", "days": None, "estimated_items": 100},
        "derivation": {
            "basis": "service_default", "summary": "preserved-store shape",
            "measurements": {}, "provenance": [],
        },
        "surfaces": [],
        "caps": {**caps, "max_items": 100, "max_pages": 4},
        "interpretation_stages": [],
        "predicted_verdict": "approved_as_proposed",
        "predicted_confidence_percent": 50,
        "prediction_basis": {},
        "verdict": "approved_as_proposed",
        "proposed_by": "fixture.plan_proposer",
        "approved_by": "owner:client",
    })
    runtime.run_until_idle()

    pending = _pending_identities(graph)
    assert "ingestion_plan_legacy_v2_comprehension" in pending
    begun = begin_deferred_plan_execution_fn(
        graph, plan_ref="ingestion_plan_legacy_v2_comprehension",
    )
    assert begun["ok"], begun
    # And the surface-level helper still resolves a head for legacy readers.
    head = current_plan_for_surface_fn(graph, SURFACE)
    assert head is not None
