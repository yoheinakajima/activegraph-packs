"""Diligence-Core Bridge fixtures.

Simulates Diligence-pack events by injecting diligence-type objects directly
into the graph (without loading the Diligence pack, which conflicts with Core
on the `derived_from` relation type). The bridge subscribes to object.created
events by type name, so it fires regardless of which pack registered the type.

Two fixtures:
  1. document_claim_bridge  — document + claim inject → source + observation created
  2. memo_risk_bridge       — memo + risk inject → artifact + evaluation created

Run with:
    python packs/bridges/fixtures/run_fixtures.py
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from activegraph import Graph, Runtime
from activegraph.packs import ObjectType, Pack
from pydantic import BaseModel

from packs.core import pack as core_pack, CoreSettings
from packs.bridges import pack as bridge_pack, DiligenceCoreBridgeSettings
from packs.bridges.diligence_core import clear_bridge_registry


# ── Minimal stub pack with Diligence-like object types ───────────────────────
# The Diligence pack conflicts with Core on the `derived_from` relation type.
# This stub pack registers only the object types needed to simulate Diligence
# events, so the bridge can be tested without loading the real Diligence pack.

class _DocumentSchema(BaseModel):
    title: str = ""
    url: str | None = None
    company_id: str | None = None
    summary: str = ""
    published_at: str | None = None


class _ClaimSchema(BaseModel):
    text: str = ""
    confidence: float = 0.75
    company_id: str | None = None
    source_document_id: str | None = None
    status: str = "unverified"


class _MemoSchema(BaseModel):
    company_id: str | None = None
    summary: str = ""
    thesis_questions_addressed: list = []
    key_claims: list = []
    open_contradictions: list = []
    risks: list = []


class _RiskSchema(BaseModel):
    title: str = ""
    description: str = ""
    severity: str = "medium"
    company_id: str | None = None
    related_claim_ids: list = []


class _StubSettings(BaseModel):
    pass


_diligence_stub_pack = Pack(
    name="diligence_stub",
    version="0.1.0",
    description="Stub pack registering Diligence object types for bridge testing.",
    object_types=[
        ObjectType(name="document", schema=_DocumentSchema, description="Diligence document"),
        ObjectType(name="claim", schema=_ClaimSchema, description="Diligence claim"),
        ObjectType(name="memo", schema=_MemoSchema, description="Diligence memo"),
        ObjectType(name="risk", schema=_RiskSchema, description="Diligence risk"),
    ],
    relation_types=[],
    behaviors=[],
    tools=[],
    policies=[],
    prompts=[],
    settings_schema=_StubSettings,
)


def _make_runtime() -> tuple[Runtime, Graph]:
    """Create a Runtime with Core + stub Diligence types + bridge."""
    clear_bridge_registry()
    graph = Graph()
    rt = Runtime(graph)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(_diligence_stub_pack, settings=_StubSettings())
    rt.load_pack(bridge_pack, settings=DiligenceCoreBridgeSettings())
    return rt, graph


def fixture_document_claim_bridge():
    sep = "=" * 60
    print(f"\n{sep}")
    print("Fixture: document_claim_bridge")
    print("  diligence document + claim → Core source + observation")
    print(f"{sep}\n")

    rt, graph = _make_runtime()

    # Simulate diligence creating a document (inject directly into graph)
    doc = graph.add_object("document", {
        "title": "Northwind Robotics — Technical Due Diligence Report",
        "url": "https://docs.example.com/northwind-dd.pdf",
        "company_id": "company-001",
        "summary": "Technical review of Northwind's robotics stack. Strong CV pipeline, "
                   "weak supply chain integration. Team has 3 ex-Boston Dynamics engineers.",
        "published_at": "2026-06-03",
    })
    rt.run_until_idle()

    # Simulate diligence creating a claim linked to the document
    claim = graph.add_object("claim", {
        "text": "Northwind has 3 ex-Boston Dynamics engineers on the core robotics team.",
        "confidence": 0.92,
        "company_id": "company-001",
        "source_document_id": doc.id,
        "status": "verified",
    })
    rt.run_until_idle()

    sources = [o for o in graph.objects() if o.type == "source" and
               (o.data or {}).get("metadata", {}).get("bridge") == "diligence_core"]
    observations = [o for o in graph.objects() if o.type == "observation" and
                    (o.data or {}).get("metadata", {}).get("bridge") == "diligence_core"]
    relations = list(graph.relations())
    derived = [r for r in relations if r.type == "derived_from"]

    print(f"  Bridge sources (document→source): {len(sources)}")
    for s in sources:
        d = s.data or {}
        print(f"    [{s.id[:8]}] kind={d.get('kind')} "
              f"url={d.get('url')!r}")
        print(f"             meta.title={d.get('metadata', {}).get('title', '')!r}")

    print(f"  Bridge observations (claim→observation): {len(observations)}")
    for o in observations:
        d = o.data or {}
        print(f"    [{o.id[:8]}] conf={d.get('confidence')} "
              f"text={d.get('text', '')[:55]!r}")

    print(f"  derived_from relations: {len(derived)}")

    ok = len(sources) >= 1 and len(observations) >= 1 and len(derived) >= 2
    print(f"\n  {'PASS' if ok else 'FAIL'}")
    return ok


def fixture_memo_risk_bridge():
    sep = "=" * 60
    print(f"\n{sep}")
    print("Fixture: memo_risk_bridge")
    print("  diligence memo + risk → Core artifact + evaluation")
    print(f"{sep}\n")

    rt, graph = _make_runtime()

    company_id = "company-002"

    # Simulate diligence creating a risk
    risk = graph.add_object("risk", {
        "title": "Single customer concentration",
        "description": "One customer accounts for 78% of ARR. Loss would be existential.",
        "severity": "high",
        "company_id": company_id,
        "related_claim_ids": [],
    })
    rt.run_until_idle()

    # Simulate diligence creating a memo
    memo = graph.add_object("memo", {
        "company_id": company_id,
        "summary": "TensorWave has compelling technology but dangerous customer concentration. "
                   "Team is strong, runway is 14 months.",
        "thesis_questions_addressed": [
            "Is the technology differentiated? Yes — custom CUDA kernels.",
            "Is the team strong? Yes — 2 ex-Google Brain.",
        ],
        "key_claims": [
            "ARR is $2.4M, growing 18% MoM",
            "Custom CUDA kernels give 3x throughput vs vLLM",
        ],
        "open_contradictions": [
            "Customer LOI vs actual signed contract unclear",
        ],
        "risks": [
            "Single customer concentration (78% ARR)",
            "14-month runway — tight for Series A",
        ],
    })
    rt.run_until_idle()

    artifacts = [o for o in graph.objects() if o.type == "artifact" and
                 (o.data or {}).get("metadata", {}).get("bridge") == "diligence_core"]
    evaluations = [o for o in graph.objects() if o.type == "evaluation" and
                   (o.data or {}).get("metadata", {}).get("bridge") == "diligence_core"]
    relations = list(graph.relations())
    derived = [r for r in relations if r.type == "derived_from"]

    print(f"  Bridge artifacts (memo→artifact): {len(artifacts)}")
    for a in artifacts:
        d = a.data or {}
        print(f"    [{a.id[:8]}] kind={d.get('kind')} title={d.get('title')!r}")
        print(f"             status={d.get('status')} content_len={len(d.get('content',''))}")

    print(f"  Bridge evaluations (risk→evaluation): {len(evaluations)}")
    for e in evaluations:
        d = e.data or {}
        print(f"    [{e.id[:8]}] judgment={d.get('judgment')} "
              f"rationale={d.get('rationale', '')[:55]!r}")

    print(f"  derived_from relations: {len(derived)}")

    ok = len(artifacts) >= 1 and len(evaluations) >= 1 and len(derived) >= 2
    print(f"\n  {'PASS' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    results = [
        fixture_document_claim_bridge(),
        fixture_memo_risk_bridge(),
    ]
    total = len(results)
    passed = sum(results)
    print(f"\n{'=' * 60}")
    print(f"Diligence-Core Bridge: {passed}/{total} fixtures passed")
    print("=" * 60)
    sys.exit(0 if passed == total else 1)
