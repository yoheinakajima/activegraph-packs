"""Web-research conformance: scope gates and the affordance, zero-key.

The deterministic classifier is the constitutional line (D060/ADR 0045):
private identifiers never join an outward query, owner exclusions reject,
sensitive topics and new entities pause as amendments, and the affordance's
source-owned gate applies all of it against the CURRENT approved plan.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parents[2]))

from activegraph import Graph, Runtime

from packs.connector_control import pack as connector_control_pack
from packs.web_research import pack
from packs.web_research.affordance import (
    WEB_UNDERSTANDING_AFFORDANCE,
    research_outward_gate,
)
from packs.web_research.campaign import scope_gate_for_query


def run_fixture() -> dict:
    runtime = Runtime(Graph())
    runtime.load_pack(connector_control_pack)
    runtime.load_pack(pack)
    runtime.run_until_idle()
    graph = runtime.graph

    scope = ["Ada Example", "@adaexample", "example.com"]
    kwargs = {
        "scope_terms": scope, "prior_query_texts": [],
        "exclusions": ["family"], "sensitive_terms": ["health"],
    }
    assert scope_gate_for_query('"Ada Example" projects', **kwargs)["verdict"] == "auto"
    assert scope_gate_for_query(
        "Ada Example ada@example.com", **kwargs
    )["verdict"] == "rejected", "a private identifier never goes outward"
    assert scope_gate_for_query(
        "Ada Example family life", **kwargs
    )["verdict"] == "rejected", "owner exclusions are final"
    assert scope_gate_for_query(
        "Ada Example health records", **kwargs
    )["verdict"] == "amendment", "sensitive topics pause for the owner"
    assert scope_gate_for_query(
        '"Completely Different Person" biography', **kwargs
    )["verdict"] == "amendment", "a new entity is a scope expansion"

    # The affordance's gate fails closed with no approved plan.
    no_plan = research_outward_gate(graph, '"Ada Example"', {})
    assert no_plan["verdict"] == "rejected"
    assert no_plan["reason_kind"] == "no_plan"

    declaration = WEB_UNDERSTANDING_AFFORDANCE
    assert declaration["drill_down"]["allowed"] is False
    assert declaration["privacy"]["outward_disclosure"] == "public_queries"
    assert "outward_query" in declaration["moves"]
    return {"gates": 5, "affordance": declaration["affordance_id"]}


if __name__ == "__main__":
    try:
        print(f"Web Research Fixtures PASS: {run_fixture()}")
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)
