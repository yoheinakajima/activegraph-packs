"""Evolution Pack authoring and request functions.

`submit_proposal_fn` is the ONE authoring entry point (design §3 stage
1): scripted generators, owner-drafted packs routed through chat, and a
future LLM author all pass through it, and everything downstream (gates,
trials, approval) treats them identically.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .materialize import bundle_hash_of


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def submit_proposal_fn(
    graph,
    *,
    pack_name: str,
    files: dict[str, str],
    gap_id: str = "",
    pack_version: str = "0.1.0",
    rationale: str = "",
    authored_by: str = "agent",
    drafting_context_id: str = "",
):
    """Store a candidate pack as artifacts + a mod_proposal.

    The bundle hash is pinned HERE, at submission, over the exact bytes
    submitted (manifest included); every later stage recomputes against
    this pin. The proposal_gatekeeper behavior fires on creation and
    runs the static gates (or suspends on tainted lineage).

    With `drafting_context_id` (llm-author-design §4): the drafting
    record is sealed before the model call and immutable after, and this
    function does two things that do not trust it blindly. It RECOMPUTES
    the injection-flag union from the objects the record admitted, read
    fresh from the graph by id, never from the record's stored
    injection_flags field, so a record-corruption bug cannot launder
    taint (the §4 analog of gap-lineage's no-model-choice-launders
    rule). And it charset-validates the admitted structured fields,
    REFUSING submission (ValueError) when a field carries prose-shaped
    text rather than an identifier, so "structured" means structured."""
    inherited_flags: list[str] = []
    if drafting_context_id:
        from .author_frame import (
            recompute_drafting_taint,
            validate_structured_fields,
        )
        record = graph.get_object(drafting_context_id)
        if record is not None:
            record_data = record.data or {}
            field_violations = validate_structured_fields(
                graph, record_data.get("structured_fields") or [])
            if field_violations:
                raise ValueError(
                    "drafting record admits non-identifier structured "
                    "fields; refusing to submit: "
                    + "; ".join(field_violations))
            # Recompute from admitted object ids, NOT the stored field.
            inherited_flags = recompute_drafting_taint(graph, record_data)

    artifact_ids = []
    for path, text in sorted(files.items()):
        artifact = graph.add_object("artifact", {
            "kind": "pack_source",
            "title": path,
            "content": text,
            "format": "toml" if path.endswith(".toml") else "python",
            "status": "draft",
            "metadata": {"pack_name": pack_name},
        })
        artifact_ids.append(str(artifact.id))

    proposal = graph.add_object("mod_proposal", {
        "gap_id": gap_id,
        "drafting_context_id": drafting_context_id,
        "pack_name": pack_name,
        "pack_version": pack_version,
        "source_artifact_ids": artifact_ids,
        "bundle_hash": bundle_hash_of(files),
        "rationale": rationale,
        "authored_by": authored_by,
        "injection_flags": inherited_flags,
        "status": "drafted",
    })
    if gap_id and graph.get_object(gap_id) is not None:
        try:
            graph.add_relation(proposal.id, gap_id, "proposes_fix_for")
        except Exception:
            pass
    return proposal


def open_reflection_gap_fn(
    graph,
    *,
    description: str,
    reviewed_result_ids: list[str],
    evidence_refs: Optional[list[str]] = None,
):
    """Open a reflection gap with DETERMINISTIC taint inheritance.

    The gap's injection_flags are the union of the flags on every
    reviewed capability_result, computed from `reviewed_result_ids`
    (the review's full input set), regardless of what the reviewer
    chose to put in `evidence_refs`. An LLM between a flagged result
    and a fresh gap cannot launder the taint away (design §3 stage 0,
    §6 T2)."""
    inherited: set[str] = set()
    for result_id in reviewed_result_ids:
        obj = graph.get_object(result_id)
        if obj is not None:
            inherited.update((obj.data or {}).get("injection_flags") or [])
    return graph.add_object("capability_gap", {
        "kind": "reflection",
        "description": description,
        "evidence_refs": list(evidence_refs or []),
        "injection_flags": sorted(inherited),
        "status": "open",
        "metadata": {"reviewed_result_ids": reviewed_result_ids},
    })


def request_adoption_fn(graph, *, proposal_id: str, proposed_by: str,
                        note: str = "") -> dict:
    """Propose adoption through the gateway: records the capability_call
    and holds it (critical is never auto-approvable; registration refused
    otherwise). Mirrors the governed-call flow the MCP gateway uses."""
    from packs.tool_gateway.gateway import decide_policy
    from packs.tool_gateway.settings import ToolGatewaySettings
    from packs.tool_gateway.tools import get_capability_spec

    spec = get_capability_spec("evolution.adopt_proposal")
    if spec is None:
        return {"error": "evolution.adopt_proposal is not registered"}
    decision = decide_policy(spec.risk_class, ToolGatewaySettings())
    call = graph.add_object("capability_call", {
        "provider_id": "",
        "provider_name": spec.provider_name,
        "capability_name": spec.capability_name,
        "input_data": {"proposal_id": proposal_id, "note": note},
        "risk_class": spec.risk_class,
        "status": "approved" if decision == "auto_approve" else "policy_checking",
        "proposed_by": proposed_by,
        "proposed_at": _now(),
        "metadata": {"initiated_by": "evolution.request_adoption"},
    })
    graph.patch_object(proposal_id, {"status": "pending_approval"})
    return {"call_id": str(call.id), "status": call.data["status"]}


TOOLS: list = []
