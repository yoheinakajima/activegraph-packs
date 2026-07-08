"""Demo-server approval channel auth (gate 4, scoped honestly).

The bearer token authenticates the CHANNEL; the principal check on
approver_ref stays the DECISION. Refusals are audited into the graph.
Session-to-principal binding remains BabyAGI chassis territory; these
tests cover exactly what the demo server claims.
"""

from __future__ import annotations

from activegraph import Graph

from packs.demo_server import check_approval_auth

TOKEN = "test-approval-token"


def _denials(graph) -> list:
    return [o for o in graph.all_objects()
            if o.type == "approval_auth_denial"]


def test_correct_token_passes():
    graph = Graph()
    ok, reason = check_approval_auth(
        graph, f"Bearer {TOKEN}", evolution_on=True, token=TOKEN)
    assert ok and not reason
    assert _denials(graph) == []


def test_missing_token_refused_and_audited():
    graph = Graph()
    ok, reason = check_approval_auth(
        graph, "", evolution_on=True, token=TOKEN)
    assert not ok and "missing" in reason
    (denial,) = _denials(graph)
    assert denial.data["reason"] == reason
    assert denial.data["path"] == "/approvals"


def test_wrong_token_refused_and_audited():
    graph = Graph()
    ok, reason = check_approval_auth(
        graph, "Bearer nope", evolution_on=True, token=TOKEN)
    assert not ok and "mismatch" in reason
    assert len(_denials(graph)) == 1


def test_no_token_with_evolution_on_refuses():
    """Self-modification approvals over an unauthenticated channel must
    not exist: the registration-refusal principle, applied to the
    transport."""
    graph = Graph()
    ok, reason = check_approval_auth(
        graph, "Bearer anything", evolution_on=True, token="")
    assert not ok and "ACTIVEGRAPH_APPROVAL_TOKEN" in reason
    assert len(_denials(graph)) == 1


def test_no_token_without_evolution_stays_open():
    """The plain demo posture, stated at boot: no token, no evolution,
    channel open (the decision check still runs underneath)."""
    graph = Graph()
    ok, reason = check_approval_auth(
        graph, "", evolution_on=False, token="")
    assert ok and not reason
    assert _denials(graph) == []
