"""Stage-2 static gates (design §3): deterministic, zero LLM, zero
execution of candidate code. First failure stops the pipeline; every
verdict is a gate_result object, pass or fail, so the audit trail shows
what was checked, not just what failed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from activegraph.packs.manifest import (
    PackManifestError,
    load_manifest,
    verify_bundle_hash,
    verify_content_hash,
)

from . import analysis
from .materialize import proposal_files, write_files
from .settings import EvolutionSettings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(graph, proposal_id: str, gate: str, verdict: str, details: str) -> None:
    result = graph.add_object("gate_result", {
        "proposal_id": proposal_id, "gate": gate, "verdict": verdict,
        "details": details[:2000], "at": _now(),
    })
    try:
        graph.add_relation(proposal_id, result.id, "gated_by")
    except Exception:
        pass


def run_static_gates(graph, proposal, settings: EvolutionSettings) -> bool:
    """Run gates 0-8 against a proposal; returns True when all pass.

    Writes a gate_result per gate; patches the proposal to `gated` on
    success, `rejected` on the first failure, `suspended` on injection
    taint (gate 8 suspends, never rejects: taint is the owner's call)."""
    proposal_id = proposal.id
    files = proposal_files(graph, proposal)

    def fail(gate: str, violations: list[str]) -> bool:
        _record(graph, proposal_id, gate, "fail", "; ".join(violations))
        graph.patch_object(proposal_id, {
            "status": "rejected",
            "status_note": f"{gate}: {violations[0]}" if violations else gate,
        })
        return False

    # Gate 0: file set (implicit in the design's stage-1 constraints).
    violations = analysis.check_file_set(files, settings.allowed_files)
    if violations:
        return fail("static:file_set", violations)
    _record(graph, proposal_id, "static:file_set", "pass", "")

    # Gate 1: manifest validity.
    root = write_files(files)
    try:
        manifest = load_manifest(root / "manifest.toml")
    except PackManifestError as exc:
        return fail("static:manifest", list(getattr(exc, "violations", [str(exc)])))
    if manifest.name != proposal.data.get("pack_name"):
        return fail("static:manifest",
                    [f"manifest name {manifest.name!r} != proposal pack_name"])
    _record(graph, proposal_id, "static:manifest", "pass", "")

    # Gate 2: hash integrity, both hashes (design §2: the bundle hash is
    # the pin; the content hash keeps manifest-vs-source honest).
    try:
        verify_content_hash(manifest, root)
        verify_bundle_hash(proposal.data.get("bundle_hash", ""), root)
    except PackManifestError as exc:
        return fail("static:hash", list(getattr(exc, "violations", [str(exc)])))
    _record(graph, proposal_id, "static:hash", "pass", "")

    # Gate 3: declared-vs-actual, two-way.
    try:
        violations = analysis.check_declared_vs_actual(files, manifest)
    except ValueError as exc:  # unparseable source
        violations = [str(exc)]
    if violations:
        return fail("static:declared_vs_actual", violations)
    _record(graph, proposal_id, "static:declared_vs_actual", "pass", "")

    # Gate 4: import allow-list.
    violations = analysis.check_imports(
        files, settings.import_allow_list, settings.fixture_extra_allow,
        pack_name=manifest.name)
    if violations:
        return fail("static:imports", violations)
    _record(graph, proposal_id, "static:imports", "pass", "")

    # Gate 5: banned constructs.
    violations = analysis.check_banned_constructs(files)
    if violations:
        return fail("static:banned_constructs", violations)
    _record(graph, proposal_id, "static:banned_constructs", "pass", "")

    # Gate 6: reserved namespaces / never-LLM-callable names.
    violations = analysis.check_reserved_namespaces(
        files, manifest, settings.reserved_namespaces)
    if violations:
        return fail("static:reserved", violations)
    _record(graph, proposal_id, "static:reserved", "pass", "")

    # Gate 7: size caps.
    violations = analysis.check_size_caps(
        files, max_total=settings.max_total_source_bytes,
        max_file=settings.max_file_bytes)
    if violations:
        return fail("static:size", violations)
    _record(graph, proposal_id, "static:size", "pass", "")

    # Gate 8: injection scan. A hit SUSPENDS (design §3: taint is
    # surfaced for the owner, never silently rejected or silently passed).
    from packs.tool_gateway.untrusted import scan_for_injection

    flags = sorted({flag for text in files.values()
                    for flag in scan_for_injection(text)})
    if flags:
        _record(graph, proposal_id, "static:injection", "suspended",
                ", ".join(flags))
        graph.patch_object(proposal_id, {
            "status": "suspended",
            "status_note": f"injection patterns in source: {', '.join(flags)}",
            "injection_flags": flags,
        })
        return False
    _record(graph, proposal_id, "static:injection", "pass", "")

    graph.patch_object(proposal_id, {"status": "gated", "status_note": ""})
    return True
