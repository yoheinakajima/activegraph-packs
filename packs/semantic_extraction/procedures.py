"""First ADR 0029 pilot: guarded dynamic-to-deterministic extraction.

The candidate is declarative graph state around a shipped deterministic parser;
no generated code is imported. Evaluation runs in a real runtime fork and only
its audit objects are fail-closed promoted into the parent.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

from .engine import parse_extractor_ref, stable_id
from .extractor import AnnotationDraft, _sentences


REQUEST_PROCEDURE_REF = "semantic.request_rule@0.1.0"
REFERENCE_REF = "semantic.llm@0.1.0"
POLICY_ID = "semantic.proceduralization@1"
_REQUEST_CUE = re.compile(
    r"\b(please|can you|could you|would you|will you|need you to|"
    r"action item|to[- ]?do|follow up|send me|let me know)\b",
    re.IGNORECASE,
)


class RequestRuleExtractorV1:
    extractor_id = "semantic.request_rule"
    extractor_version = "0.1.0"

    def implemented_facets(self) -> tuple[str, ...]:
        return ("relation_mention",)

    def config(self) -> dict[str, Any]:
        return {
            "policy_id": POLICY_ID,
            "cue_pattern_id": "communication.explicit_request@1",
            "sentence_boundary": "semantic.deterministic._sentences@1",
        }

    def extract(
        self, content: str, metadata: dict[str, Any], facets: tuple[str, ...]
    ) -> list[AnnotationDraft]:
        del metadata
        if "relation_mention" not in facets:
            return []
        rows = []
        for start, end, sentence in _sentences(content):
            match = _REQUEST_CUE.search(sentence)
            if match is None:
                continue
            rows.append(AnnotationDraft(
                facet="relation_mention",
                body={
                    "text": sentence,
                    "subject": "sender",
                    "predicate": "requests",
                    "object": sentence,
                },
                start=start,
                end=end,
                exact=sentence,
                confidence=0.85,
                metadata={
                    "procedure_ref": REQUEST_PROCEDURE_REF,
                    "cue": match.group(0).lower(),
                },
            ))
        return rows


def shape_fingerprint(evidence_data: dict[str, Any]) -> str:
    metadata = dict(evidence_data.get("normalized_metadata") or {})
    material = {
        "source_category": evidence_data.get("source_category"),
        "media_type": evidence_data.get("media_type"),
        "interpretation_family": metadata.get("interpretation_family"),
        "direction": metadata.get("direction"),
        "message_kind": metadata.get("message_kind"),
        "injection": bool(metadata.get("injection_flags")),
        "shape_version": metadata.get("shape_version") or "unknown",
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _objects(reader, object_type: str):
    try:
        return list(reader.objects(type=object_type))
    except Exception:
        return []


def _reference_annotations(
    reader, evidence_id: str, *, facet: str, reference_ref: str
) -> list[Any]:
    extractor_id, version = parse_extractor_ref(reference_ref)
    return [
        obj for obj in _objects(reader, "semantic_annotation")
        if obj.data.get("evidence_id") == evidence_id
        and obj.data.get("facet") == facet
        and obj.data.get("extractor_id") == extractor_id
        and obj.data.get("extractor_version") == version
        and obj.data.get("status") == "active"
    ]


def _accepted_corrections(
    reader, annotation_ids: set[str]
) -> tuple[list[str], list[str], set[str]]:
    accepted: list[str] = []
    rejected: list[str] = []
    accepted_subjects: set[str] = set()
    for obj in _objects(reader, "evaluation"):
        if obj.data.get("subject_type") != "semantic_annotation":
            continue
        subject_id = str(obj.data.get("subject_id") or "")
        if subject_id not in annotation_ids:
            continue
        judgment = str(obj.data.get("judgment") or "")
        if judgment in {"accepted", "completed_successfully", "correct"}:
            accepted.append(obj.id)
            accepted_subjects.add(subject_id)
        elif judgment in {"rejected", "needs_revision", "incorrect"}:
            rejected.append(obj.id)
    return accepted, rejected, accepted_subjects


def synthesize_request_procedure_fn(
    graph,
    *,
    witness_evidence_ids: list[str],
    held_out_evidence_ids: list[str],
    counterexample_evidence_ids: list[str],
    created_by: str,
    reference_ref: str = REFERENCE_REF,
) -> dict[str, Any]:
    """Declare a candidate only after reference annotations were reviewed."""
    if len(witness_evidence_ids) < 3:
        raise ValueError("procedure synthesis requires at least three witnesses")
    if len(held_out_evidence_ids) < 2:
        raise ValueError("procedure synthesis requires at least two held-out examples")
    if not counterexample_evidence_ids:
        raise ValueError("procedure synthesis requires a counterexample set")
    if not created_by:
        raise ValueError("procedure synthesis requires a named author")
    all_positive = list(dict.fromkeys([*witness_evidence_ids, *held_out_evidence_ids]))
    evidence = {}
    reference_annotations = []
    for evidence_id in [*all_positive, *counterexample_evidence_ids]:
        obj = graph.get_object(evidence_id)
        if obj is None or obj.type != "activity_evidence":
            raise ValueError(f"no activity_evidence {evidence_id!r}")
        evidence[evidence_id] = obj
    for evidence_id in all_positive:
        rows = _reference_annotations(
            graph, evidence_id, facet="relation_mention", reference_ref=reference_ref
        )
        if not rows:
            raise ValueError(f"positive example {evidence_id!r} has no reference annotation")
        reference_annotations.extend(rows)
    annotation_ids = {obj.id for obj in reference_annotations}
    accepted, rejected, accepted_subjects = _accepted_corrections(graph, annotation_ids)
    if rejected:
        raise ValueError("reference set contains rejected/corrected annotations")
    if annotation_ids - accepted_subjects:
        raise ValueError("every reference annotation requires an accepted evaluation")

    fingerprints = sorted({
        shape_fingerprint(evidence[evidence_id].data)
        for evidence_id in witness_evidence_ids
    })
    identity = stable_id(
        "procedure_candidate", REQUEST_PROCEDURE_REF, reference_ref,
        *sorted(evidence[eid].data["revision_id"] for eid in all_positive),
    )
    existing = next(
        (obj for obj in graph.objects(type="procedure_candidate")
         if obj.data.get("candidate_identity") == identity),
        None,
    )
    if existing is not None:
        return {"ok": True, "created": False, "candidate_id": existing.id, "guard_id": existing.data["guard_id"]}
    guard = graph.add_object("procedure_guard", {
        "guard_identity": stable_id(
            "procedure_guard", REQUEST_PROCEDURE_REF, *fingerprints
        ),
        "guard_kind": "shape_and_cue",
        "source_category": "communication",
        "facet": "relation_mention",
        "shape_fingerprints": fingerprints,
        "cue_pattern_id": "communication.explicit_request@1",
        "max_content_chars": 2_000,
        "false_admissions": 0,
        "abstentions": 0,
        "calibration_examples": len(all_positive) + len(counterexample_evidence_ids),
        "metadata": {"policy_id": POLICY_ID},
    })
    candidate = graph.add_object("procedure_candidate", {
        "candidate_identity": identity,
        "domain": "semantic_extraction",
        "procedure_kind": "deterministic_parser",
        "candidate_ref": REQUEST_PROCEDURE_REF,
        "reference_ref": reference_ref,
        "facet": "relation_mention",
        "semantic_objective": "extract explicit inbound communication requests",
        "equivalence_rule": "facet_exact_selector",
        "input_schema": {"type": "activity_evidence", "source_category": "communication"},
        "output_schema": {"type": "semantic_annotation", "facet": "relation_mention"},
        "allowed_effects": ["semantic_annotation.create", "extraction_run.create"],
        "required_capabilities": [],
        "action_class": "R0",
        "resource_bounds": {"provider_calls": 0, "max_content_chars": 2_000, "max_annotations": 6},
        "witness_evidence_ids": list(witness_evidence_ids),
        "held_out_evidence_ids": list(held_out_evidence_ids),
        "counterexample_evidence_ids": list(counterexample_evidence_ids),
        "reference_annotation_ids": sorted(annotation_ids),
        "correction_evaluation_ids": sorted(accepted),
        "guard_id": guard.id,
        "fallback_ref": reference_ref,
        "status": "candidate",
        "status_reason": "awaiting forked held-out evaluation",
        "created_by": created_by,
        "metadata": {"policy_id": POLICY_ID, "drift_deopt_threshold": 3},
    })
    graph.add_relation(candidate.id, guard.id, "procedure_has_guard")
    return {"ok": True, "created": True, "candidate_id": candidate.id, "guard_id": guard.id}


def _selector_keys(rows: list[Any]) -> set[tuple[str, int, int, str]]:
    return {
        (
            str(row.data.get("facet")), int((row.data.get("selector") or {}).get("start", 0)),
            int((row.data.get("selector") or {}).get("end", 0)),
            str((row.data.get("selector") or {}).get("exact", "")),
        )
        for row in rows
    }


def guard_admits(guard_data: dict[str, Any], evidence_data: dict[str, Any]) -> tuple[bool, str]:
    if evidence_data.get("source_category") != guard_data.get("source_category"):
        return False, "outside_domain"
    content = str(evidence_data.get("normalized_content") or "")
    fingerprint = shape_fingerprint(evidence_data)
    if fingerprint not in set(guard_data.get("shape_fingerprints") or []):
        return False, "shape_drift"
    if len(content) > int(guard_data.get("max_content_chars") or 0):
        return False, "shape_drift"
    if _REQUEST_CUE.search(content) is None:
        return False, "guard_abstained"
    return True, "admitted"


def _evaluate_set(reader, candidate, guard, evidence_ids: list[str]) -> dict[str, Any]:
    extractor = RequestRuleExtractorV1()
    matched = 0
    total = 0
    mismatches = 0
    abstentions = 0
    failures = []
    for evidence_id in evidence_ids:
        evidence = reader.get_object(evidence_id)
        reference = _reference_annotations(
            reader, evidence_id, facet=candidate.data["facet"],
            reference_ref=candidate.data["reference_ref"],
        )
        expected = _selector_keys(reference)
        admitted, reason = guard_admits(guard.data, evidence.data)
        if not admitted:
            abstentions += 1
            actual: set[tuple[str, int, int, str]] = set()
        else:
            actual = {
                (draft.facet, draft.start, draft.end, draft.exact)
                for draft in extractor.extract(
                    str(evidence.data.get("normalized_content") or ""),
                    dict(evidence.data.get("normalized_metadata") or {}),
                    (candidate.data["facet"],),
                )
            }
        total += max(1, len(expected | actual))
        matched += len(expected & actual)
        mismatches += len(expected ^ actual)
        if expected != actual:
            failures.append({
                "evidence_id": evidence_id, "guard": reason,
                "expected": len(expected), "actual": len(actual),
            })
    parity = 1_000 if total == 0 else (matched * 1_000) // total
    return {
        "parity_milli": parity, "selector_mismatches": mismatches,
        "guard_abstentions": abstentions, "failures": failures,
    }


def evaluate_request_procedure_fn(runtime, candidate_id: str) -> dict[str, Any]:
    """Evaluate in a real fork, then promote only the evaluation audit state."""
    parent = runtime
    candidate = parent.graph.get_object(candidate_id)
    if candidate is None or candidate.type != "procedure_candidate":
        raise ValueError(f"no procedure_candidate {candidate_id!r}")
    if candidate.data.get("status") != "candidate":
        raise ValueError("procedure evaluation requires candidate status")
    tip = parent.graph.events[-1].id
    fork = parent.fork(at_event=tip, label=f"procedure-eval:{candidate_id}", behaviors=[])
    fork_candidate = fork.graph.get_object(candidate_id)
    guard = fork.graph.get_object(str(fork_candidate.data.get("guard_id") or ""))
    if guard is None:
        raise ValueError("procedure guard is missing")
    witness = _evaluate_set(
        fork.graph, fork_candidate, guard, list(fork_candidate.data["witness_evidence_ids"])
    )
    heldout = _evaluate_set(
        fork.graph, fork_candidate, guard, list(fork_candidate.data["held_out_evidence_ids"])
    )
    counter = _evaluate_set(
        fork.graph, fork_candidate, guard, list(fork_candidate.data["counterexample_evidence_ids"])
    )
    false_admissions = sum(
        1 for row in counter["failures"] if row["actual"] > 0 and row["expected"] == 0
    )
    false_rejections = sum(
        1 for row in [*witness["failures"], *heldout["failures"]]
        if row["expected"] > 0 and row["actual"] == 0
    )
    failures = [*witness["failures"], *heldout["failures"], *counter["failures"]]
    verdict = "pass" if (
        witness["parity_milli"] == 1_000
        and heldout["parity_milli"] == 1_000
        and false_admissions == 0
        and false_rejections == 0
        and counter["selector_mismatches"] == 0
    ) else "fail"
    evaluation = fork.graph.add_object("procedure_evaluation", {
        "evaluation_identity": stable_id(
            "procedure_evaluation", candidate_id, fork.run_id, tip
        ),
        "candidate_id": candidate_id,
        "fork_run_id": fork.run_id,
        "forked_at_event_id": tip,
        "witness_count": len(fork_candidate.data["witness_evidence_ids"]),
        "held_out_count": len(fork_candidate.data["held_out_evidence_ids"]),
        "counterexample_count": len(fork_candidate.data["counterexample_evidence_ids"]),
        "witness_parity_milli": witness["parity_milli"],
        "held_out_parity_milli": heldout["parity_milli"],
        "false_admissions": false_admissions,
        "false_rejections": false_rejections,
        "selector_mismatches": witness["selector_mismatches"] + heldout["selector_mismatches"] + counter["selector_mismatches"],
        "guard_abstentions": witness["guard_abstentions"] + heldout["guard_abstentions"] + counter["guard_abstentions"],
        "candidate_calls": (
            len(fork_candidate.data["witness_evidence_ids"])
            + len(fork_candidate.data["held_out_evidence_ids"])
            + len(fork_candidate.data["counterexample_evidence_ids"])
            - counter["guard_abstentions"]
        ),
        "reference_calls": 0,
        "verdict": verdict,
        "failures": failures,
        "evidence_refs": [
            *fork_candidate.data["witness_evidence_ids"],
            *fork_candidate.data["held_out_evidence_ids"],
            *fork_candidate.data["counterexample_evidence_ids"],
        ],
        "correction_evaluation_ids": list(fork_candidate.data["correction_evaluation_ids"]),
        "promote_marker_event_id": None,
        "metadata": {
            "policy_id": POLICY_ID, "equivalence": "facet_exact_selector",
            "candidate_provider_calls": 0, "reference_outputs": "recorded",
        },
    })
    fork.graph.add_relation(candidate_id, evaluation.id, "procedure_evaluated_by")
    fork.graph.patch_object(
        candidate_id,
        {"status": "evaluated" if verdict == "pass" else "demoted",
         "status_reason": f"forked evaluation {verdict}"},
    )
    plan = parent.promote(fork, dry_run=True)
    if not plan.is_promotable:
        raise RuntimeError(f"procedure evaluation promotion conflict: {plan.conflicts}")
    promoted = parent.promote(fork)
    parent.graph.patch_object(
        evaluation.id, {"promote_marker_event_id": promoted.marker_event_id}
    )
    return {
        "ok": verdict == "pass", "evaluation_id": evaluation.id,
        "verdict": verdict, "fork_run_id": fork.run_id,
        "promote_marker_event_id": promoted.marker_event_id,
    }


def promote_request_procedure_fn(
    graph, candidate_id: str, evaluation_id: str, *, approver: str
) -> dict[str, Any]:
    if not approver:
        raise ValueError("procedure promotion requires a named approver")
    candidate = graph.get_object(candidate_id)
    evaluation = graph.get_object(evaluation_id)
    if candidate is None or candidate.type != "procedure_candidate":
        raise ValueError("procedure candidate is missing")
    if evaluation is None or evaluation.type != "procedure_evaluation":
        raise ValueError("procedure evaluation is missing")
    if evaluation.data.get("candidate_id") != candidate_id or evaluation.data.get("verdict") != "pass":
        raise ValueError("procedure promotion requires its own passing evaluation")
    if (
        int(evaluation.data.get("held_out_parity_milli") or 0) != 1_000
        or int(evaluation.data.get("false_admissions") or 0) != 0
        or int(evaluation.data.get("false_rejections") or 0) != 0
    ):
        raise ValueError("procedure promotion thresholds are not met")
    for prior in graph.objects(type="procedure_candidate"):
        if (
            prior.id != candidate_id
            and prior.data.get("domain") == candidate.data.get("domain")
            and prior.data.get("facet") == candidate.data.get("facet")
            and prior.data.get("status") == "promoted"
        ):
            graph.patch_object(prior.id, {
                "status": "demoted", "status_reason": f"superseded by {candidate_id}"
            })
    graph.patch_object(candidate_id, {
        "status": "promoted",
        "status_reason": f"approved by {approver} citing {evaluation_id}",
        "metadata": {
            **dict(candidate.data.get("metadata") or {}),
            "promotion_evaluation_id": evaluation_id,
            "approved_by": approver,
        },
    })
    graph.add_object("annotation_extractor_state", {
        "state_identity": stable_id(
            "annotation_extractor_state", REQUEST_PROCEDURE_REF, "promoted", evaluation_id
        ),
        "extractor_id": "semantic.request_rule",
        "extractor_version": "0.1.0",
        "status": "promoted",
        "reason": f"guarded procedure approved by {approver}",
        "metadata": {"procedure_candidate_id": candidate_id, "evaluation_id": evaluation_id},
    })
    return {"ok": True, "candidate_id": candidate_id, "procedure_ref": REQUEST_PROCEDURE_REF}


def active_procedure_for(reader, evidence_data: dict[str, Any], facet: str):
    candidates = [
        obj for obj in _objects(reader, "procedure_candidate")
        if obj.data.get("status") == "promoted" and obj.data.get("facet") == facet
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda obj: str(obj.data.get("candidate_identity")))
    candidate = candidates[-1]
    guard = next(
        (obj for obj in _objects(reader, "procedure_guard")
         if obj.id == candidate.data.get("guard_id")),
        None,
    )
    if guard is None:
        return None
    admitted, reason = guard_admits(guard.data, evidence_data)
    return candidate, guard, admitted, reason


def record_deoptimization(
    graph, reader, candidate, guard, evidence_obj, reason: str
) -> Optional[str]:
    if reason == "outside_domain":
        return None
    normalized = "shape_drift" if reason == "shape_drift" else "guard_abstained"
    identity = stable_id(
        "procedure_deoptimization", candidate.id,
        evidence_obj.data.get("revision_id"), normalized,
    )
    existing = next(
        (obj for obj in _objects(reader, "procedure_deoptimization")
         if obj.data.get("deoptimization_identity") == identity),
        None,
    )
    if existing is not None:
        return existing.id
    prior = [
        obj for obj in _objects(reader, "procedure_deoptimization")
        if obj.data.get("candidate_id") == candidate.id
        and obj.data.get("reason") == "shape_drift"
    ]
    deopt = graph.add_object("procedure_deoptimization", {
        "deoptimization_identity": identity,
        "candidate_id": candidate.id,
        "evidence_id": evidence_obj.id,
        "reason": normalized,
        "guard_id": guard.id,
        "fallback_ref": candidate.data["fallback_ref"],
        "fallback_status": "requested",
        "fallback_run_ids": [],
        "input_fingerprint": shape_fingerprint(evidence_obj.data),
        "evidence_refs": [evidence_obj.id],
        "metadata": {"policy_id": POLICY_ID},
    })
    graph.add_relation(candidate.id, deopt.id, "procedure_deoptimized_by")
    threshold = int((candidate.data.get("metadata") or {}).get("drift_deopt_threshold") or 3)
    if normalized == "shape_drift" and len(prior) + 1 >= threshold:
        graph.patch_object(candidate.id, {
            "status": "demoted",
            "status_reason": f"demoted after {threshold} shape-drift deoptimizations",
        })
    return deopt.id


__all__ = [
    "REQUEST_PROCEDURE_REF", "REFERENCE_REF", "RequestRuleExtractorV1",
    "active_procedure_for", "evaluate_request_procedure_fn", "guard_admits",
    "promote_request_procedure_fn", "record_deoptimization",
    "shape_fingerprint", "synthesize_request_procedure_fn",
]
