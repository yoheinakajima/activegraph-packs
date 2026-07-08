"""Codebase Pack fixtures — v0.1.

Fixture 1: repo_and_issue_tracking
  A repo manifest source triggers repo_ingester → Repo.
  GitHub webhook issue event triggers issue_tracker → Issue + Core task.
  ADR file source triggers adr_extractor → ArchitectureDecision.
  Dependency metadata triggers dependency_auditor → Dependency objects.

Fixture 2: code_change_tracking
  GitHub push webhook triggers change_summarizer → CodeChange.

Run:
    python packs/codebase/fixtures/run_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

from activegraph import Graph, Runtime
from packs.core import pack as core_pack, CoreSettings
from packs.codebase import pack as codebase_pack, CodebaseSettings
from packs.codebase.behaviors import clear_codebase_registry
from packs.codebase.tools import (
    create_repo_fn,
    create_issue_fn,
    ingest_repo_file_fn,
    ingest_github_webhook_fn,
)


def run_repo_and_issue_tracking() -> bool:
    print("\n" + "=" * 60)
    print("Fixture: repo_and_issue_tracking")
    print("  repo manifest → Repo + Dependencies | issue event → Issue + task | ADR → ArchDecision")
    print("=" * 60)

    clear_codebase_registry()

    graph = Graph()
    rt = Runtime(graph)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(codebase_pack, settings=CodebaseSettings(
        auto_create_issues_as_tasks=True,
        adr_path_patterns=["docs/adr", "adr/"],
        vulnerability_severity_threshold="medium",
    ))

    # 1. Ingest repo manifest
    create_repo_fn(
        graph,
        full_name="acme-corp/inference-engine",
        description="High-performance LLM inference engine",
        language="python",
        url="https://github.com/acme-corp/inference-engine",
        dependencies=[
            {"name": "torch", "version": "2.1.0", "kind": "direct", "ecosystem": "pypi"},
            {"name": "numpy", "version": "1.26.0", "kind": "direct", "ecosystem": "pypi"},
            {"name": "requests", "version": "2.31.0", "kind": "direct", "ecosystem": "pypi",
             "vulnerability": {"severity": "high", "summary": "CVE-2023-XXXX: SSRF via redirects"}},
        ],
    )
    rt.run_until_idle()

    repos = list(graph.objects(type="repo"))
    deps = list(graph.objects(type="dependency"))

    print(f"\n  After repo manifest:")
    print(f"  repos:        {len(repos)}")
    for r in repos:
        print(f"    '{r.data.get('full_name')}' lang={r.data.get('language')}")
    print(f"  dependencies: {len(deps)}")
    for d in deps[:5]:
        vuln = "⚠ VULN" if d.data.get("has_known_vulnerability") else "ok"
        print(f"    {d.data.get('name')}=={d.data.get('version')} [{vuln}]")

    # 2. Ingest GitHub issue
    create_issue_fn(
        graph,
        repo_full_name="acme-corp/inference-engine",
        issue_number=42,
        title="Memory leak in batch inference mode",
        body="When running batch sizes > 32, memory usage grows unboundedly. Affects CUDA backend.",
        state="open",
        labels=["bug", "performance"],
        author_ref="eng-alice",
    )
    rt.run_until_idle()

    issues = list(graph.objects(type="issue"))
    tasks = list(graph.objects(type="task"))

    print(f"\n  After issue ingestion:")
    print(f"  issues: {len(issues)}")
    for i in issues:
        print(f"    #{i.data.get('issue_number')} '{i.data.get('title')}' state={i.data.get('state')}")
        print(f"    labels={i.data.get('labels')} task_id={'set' if i.data.get('task_id') else 'none'}")
    print(f"  core tasks from issues: {len(tasks)}")

    # 3. Ingest ADR file
    adr_content = """# ADR-001: Use CUDA-optimized attention kernels

## Context
Our inference engine needs to handle batch sizes up to 256 efficiently.
Standard PyTorch attention is not optimized for our memory access patterns.

## Decision
We will use FlashAttention v2 as our primary attention implementation.
For CPU fallback, we use standard scaled dot-product attention.

## Consequences
- Faster inference on CUDA devices (40% improvement in benchmarks)
- Additional dependency on flash-attn package
- CPU performance unchanged
"""
    ingest_repo_file_fn(
        graph,
        repo_full_name="acme-corp/inference-engine",
        path="docs/adr/001-attention-kernels.md",
        content=adr_content,
        language="markdown",
    )
    rt.run_until_idle()

    adrs = list(graph.objects(type="architecture_decision"))
    print(f"\n  After ADR ingestion:")
    print(f"  architecture_decisions: {len(adrs)}")
    for a in adrs:
        print(f"    [{a.data.get('status')}] ADR-{a.data.get('adr_number')} '{a.data.get('title')[:50]}'")

    # Check relations
    all_rels = list(graph.relations())
    rel_types = {r.type for r in all_rels}
    print(f"\n  relation types seen: {sorted(rel_types)}")

    failures = []
    if not repos:
        failures.append("repo_ingester created no Repo objects")
    if not deps:
        failures.append("dependency_auditor created no Dependency objects")
    vuln_deps = [d for d in deps if d.data.get("has_known_vulnerability")]
    if not vuln_deps:
        failures.append("dependency_auditor did not flag the vulnerable dependency")
    if not issues:
        failures.append("issue_tracker created no Issue objects")
    if not tasks:
        failures.append("issue_tracker did not create a Core task (auto_create_issues_as_tasks)")
    if not adrs:
        failures.append("adr_extractor created no ArchitectureDecision objects")
    if "issue_in_repo" not in rel_types:
        failures.append("Missing relation: issue_in_repo")
    if "adr_in_repo" not in rel_types:
        failures.append("Missing relation: adr_in_repo")
    if "repo_depends_on" not in rel_types:
        failures.append("Missing relation: repo_depends_on")

    if failures:
        print("  FAIL:")
        for f in failures:
            print(f"    {f}")
        return False

    print("  PASS")
    return True


def run_code_change_tracking() -> bool:
    print("\n" + "=" * 60)
    print("Fixture: code_change_tracking")
    print("  push webhook → CodeChange")
    print("=" * 60)

    clear_codebase_registry()

    graph = Graph()
    rt = Runtime(graph)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(codebase_pack, settings=CodebaseSettings())

    # First create the repo
    create_repo_fn(graph, full_name="acme-corp/inference-engine")
    rt.run_until_idle()

    # Then ingest a push webhook
    ingest_github_webhook_fn(
        graph,
        event="push",
        payload={
            "repository": {"full_name": "acme-corp/inference-engine"},
            "after": "abc123def456",
            "pusher": {"name": "eng-bob"},
            "commits": [
                {
                    "id": "abc123def456",
                    "message": "Fix memory leak in batch inference mode (#42)",
                    "added": [],
                    "modified": ["src/inference/batch.py", "src/inference/cuda_backend.py"],
                    "removed": [],
                },
                {
                    "id": "def456ghi789",
                    "message": "Add test coverage for batch sizes > 32",
                    "added": ["tests/test_batch_memory.py"],
                    "modified": [],
                    "removed": [],
                },
            ],
        },
    )
    rt.run_until_idle()

    changes = list(graph.objects(type="code_change"))
    repos = list(graph.objects(type="repo"))

    print(f"\n  After push webhook:")
    print(f"  repos:        {len(repos)}")
    print(f"  code_changes: {len(changes)}")
    for c in changes:
        print(f"    '{c.data.get('summary')[:60]}'")
        print(f"    files_changed={c.data.get('files_changed')}")
        print(f"    sha={c.data.get('commit_sha')} author={c.data.get('author_ref')}")

    all_rels = list(graph.relations())
    rel_types = {r.type for r in all_rels}
    print(f"\n  relation types seen: {sorted(rel_types)}")

    failures = []
    if not changes:
        failures.append("change_summarizer created no CodeChange objects")
    else:
        c = changes[0]
        if not c.data.get("commit_sha"):
            failures.append("CodeChange missing commit_sha")
        if not c.data.get("files_changed"):
            failures.append("CodeChange missing files_changed")
    if "change_in_repo" not in rel_types:
        failures.append("Missing relation: change_in_repo")

    if failures:
        print("  FAIL:")
        for f in failures:
            print(f"    {f}")
        return False

    print("  PASS")
    return True


def run_all() -> None:
    results = [
        run_repo_and_issue_tracking(),
        run_code_change_tracking(),
    ]
    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"Codebase Pack: {passed}/{total} fixtures passed")
    print("=" * 60 + "\n")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    run_all()
