"""Evolution Pack object and relation types (docs/evolution-design.md §2)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from activegraph.packs import ObjectType, RelationType


class CapabilityGap(BaseModel):
    """Something the assistant could not do."""

    kind: str = Field(description="tool_failure | unhandled_intent | reflection | owner_request")
    description: str = Field(default="")
    evidence_refs: list[str] = Field(default_factory=list)
    injection_flags: list[str] = Field(
        default_factory=list,
        description="Deterministically inherited taint (design §3 stage 0).",
    )
    status: str = Field(default="open", description="open | addressed | dismissed")
    frame_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DraftingContext(BaseModel):
    """What an author READ before it wrote (llm-author-design §4).

    Written BEFORE the model call by the author frame's assembly code;
    the proposal inherits `injection_flags` as the union over every
    admitted object, computed from this record and never from anything
    the model reports. The decision surface renders it next to the
    diff, so the owner sees what the author read alongside what it
    wrote. The scripted author writes one too (model="scripted"), so
    the render path is exercised long before an LLM author exists."""

    charter_hash: str = Field(
        default="", description="sha256 of the author's system prompt.")
    gap_id: str = Field(default="")
    structured_fields: list[str] = Field(
        default_factory=list,
        description="Section (b): object-id:field-path entries admitted "
                    "as structured gap evidence.")
    surface_sources: list[str] = Field(
        default_factory=list,
        description="Section (c): manifests, schemas, contract excerpts "
                    "(repo-shipped or loader-introspected).")
    owner_input_ids: list[str] = Field(
        default_factory=list,
        description="Section (d): verified-owner chat_input ids, the one "
                    "free-text origin.")
    injection_flags: list[str] = Field(
        default_factory=list,
        description="Union of flags found on any admitted object.")
    model: str = Field(
        default="scripted",
        description="Author identity: 'scripted', 'owner', or an LLM id.")
    at: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModProposal(BaseModel):
    """One candidate pack version."""

    gap_id: str = Field(default="")
    drafting_context_id: str = Field(
        default="",
        description="The drafting_context record behind this draft "
                    "(empty for pre-record scripted submissions).")
    pack_name: str
    pack_version: str = Field(default="0.1.0")
    source_artifact_ids: list[str] = Field(default_factory=list)
    bundle_hash: str = Field(
        default="",
        description="The §2 pin: manifest-spec §4 walk INCLUDING manifest.toml.",
    )
    rationale: str = Field(default="")
    authored_by: str = Field(default="agent")
    injection_flags: list[str] = Field(default_factory=list)
    status: str = Field(
        default="drafted",
        description=("drafted | gated | rejected | trialed | pending_approval | "
                     "adopting | promoted | conflict | suspended | denied | "
                     "disabled | needs_owner"),
    )
    status_note: str = Field(default="")
    frame_id: Optional[str] = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GateResult(BaseModel):
    """One gate's verdict on one proposal."""

    proposal_id: str
    gate: str = Field(description="static:<name> | fixtures | in_sample | held_out")
    verdict: str = Field(description="pass | fail | suspended")
    details: str = Field(default="")
    at: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModTrial(BaseModel):
    """One fork trial of one proposal."""

    proposal_id: str
    fork_run_id: str = Field(default="")
    forked_at_event: str = Field(default="")
    eval_summary: dict[str, Any] = Field(default_factory=dict)
    diff_summary: dict[str, Any] = Field(default_factory=dict)
    failures: list[dict[str, Any]] = Field(default_factory=list)
    verdict: str = Field(default="", description="pass | fail")
    at: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModPromotion(BaseModel):
    """A pack adoption, recorded AT LOAD TIME (design §3 stage 5 step 4:
    the pack must be trackable even if the real promote aborts)."""

    proposal_id: str
    trial_id: str = Field(default="")
    pack_name: str = Field(default="")
    fork_run_id: str = Field(default="")
    promote_marker_event_id: str = Field(default="")
    applied_counts: dict[str, Any] = Field(default_factory=dict)
    bundle_hash: str = Field(default="")
    status: str = Field(default="loading", description="loading | active | disabled")
    at: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModRollback(BaseModel):
    """A disable/rollback action."""

    promotion_id: str
    method: str = Field(default="disable_pack")
    reason: str = Field(default="")
    at: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdoptionTicket(BaseModel):
    """Phase-one output of the governed adoption/disable capabilities.

    Born ONLY from an approved capability call; consumed by the chassis's
    process_adoption_tickets() between frames (design §3 stage 5,
    two-phase)."""

    kind: str = Field(default="adopt", description="adopt | disable")
    proposal_id: str = Field(default="")
    promotion_id: str = Field(default="")
    call_id: str = Field(default="")
    reason: str = Field(default="")
    status: str = Field(default="open", description="open | done | aborted")
    status_note: str = Field(default="")
    at: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


OBJECT_TYPES = [
    ObjectType(name="capability_gap", schema=CapabilityGap,
               description="A detected capability gap (evolution loop stage 0)."),
    ObjectType(name="drafting_context", schema=DraftingContext,
               description="What an author read before it wrote: origins, "
                           "charter hash, deterministic taint union "
                           "(llm-author-design §4)."),
    ObjectType(name="mod_proposal", schema=ModProposal,
               description="A candidate agent-authored pack, pinned by bundle hash."),
    ObjectType(name="gate_result", schema=GateResult,
               description="One gate's verdict on a proposal (audit-first)."),
    ObjectType(name="mod_trial", schema=ModTrial,
               description="A fork trial: run id, eval summary, diff, verdict."),
    ObjectType(name="mod_promotion", schema=ModPromotion,
               description="An adoption record, created at load time, the durable "
                           "registry boot re-loads from."),
    ObjectType(name="mod_rollback", schema=ModRollback,
               description="A disable/rollback action."),
    ObjectType(name="adoption_ticket", schema=AdoptionTicket,
               description="Governed phase-one request the chassis executes "
                           "out-of-frame."),
]

RELATION_TYPES = [
    RelationType(name="proposes_fix_for", source_types=("mod_proposal",),
                 target_types=("capability_gap",),
                 description="A proposal addresses a gap."),
    RelationType(name="gated_by", source_types=("mod_proposal",),
                 target_types=("gate_result",),
                 description="A proposal's gate verdicts."),
    RelationType(name="trialed_in", source_types=("mod_proposal",),
                 target_types=("mod_trial",),
                 description="A proposal's fork trials."),
    RelationType(name="promoted_as", source_types=("mod_proposal",),
                 target_types=("mod_promotion",),
                 description="A proposal's adoption record."),
    RelationType(name="rolled_back_by", source_types=("mod_promotion",),
                 target_types=("mod_rollback",),
                 description="A promotion's rollback record."),
]
