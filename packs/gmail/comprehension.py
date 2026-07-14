"""Sent-mail comprehension: Gmail's recipe and consent plan (ADR 0045 §3–4).

Gmail owns exactly what is service-specific here — the consent plan for the
latest-N sent messages (canonical Sent semantics via the ``in:sent`` search
scope, never a UI label string) and the eligible-item selection rule over the
materialized conversation family. The staged reduction itself is
``subject_synthesis.comprehension`` machinery; this module only declares the
recipe.

The comprehension input is a derived, bounded view: the family hygiene layer
already stripped quoted history, forwarded bodies, and signatures
deterministically, and the original messages stay untouched as evidence.
"""

from __future__ import annotations

from typing import Any, Optional

from packs.subject_synthesis.comprehension import register_comprehension_recipe

from .settings import GmailSettings


SENT_COMPREHENSION_RECIPE_ID = "gmail_sent_v1"
SENT_COMPREHENSION_PROPOSER = "gmail.sent_comprehension@0.1.0"
DEFAULT_SENT_COUNT = 100


def propose_gmail_sent_comprehension_plan_fn(
    graph,
    *,
    source_surface_id: str,
    account_ref: str,
    count: int = DEFAULT_SENT_COUNT,
    settings: Optional[GmailSettings] = None,
    reader=None,
) -> dict[str, Any]:
    """The consent plan: read the latest ``count`` messages the owner SENT.

    The plan is editable (a smaller count is a caps edit), receipted, and
    discloses exactly what leaves the machine: the owner's authored text goes
    to the configured model provider for summarization; recipients appear as
    identity/domain only; drafts, automated outbound, and messages the owner
    did not author are excluded; message bodies persist locally as
    content-addressed replay artifacts and every summary row cites its
    message evidence. Decline, a smaller count, and later execution are all
    first-class outcomes of the same plan lifecycle.
    """
    from packs.connector_control.plans import propose_ingestion_plan_fn
    from packs.llm_provider import (
        configured_llm_provider, resolve_model_for_role,
    )

    view = reader or graph
    configured = settings or GmailSettings()
    policy = next(
        (
            obj for obj in view.objects(type="connector_operational_policy")
            if obj.data.get("status") == "active"
        ),
        None,
    )
    if policy is None:
        raise ValueError("no active connector_operational_policy")
    ceiling_items = int(policy.data.get("max_acquisition_items") or 250)
    count = max(1, min(int(count), ceiling_items))
    page_size = int(configured.default_page_size)
    max_pages = min(
        -(-count // page_size),  # ceil
        int(policy.data.get("max_acquisition_pages") or 10),
    )
    resolved = configured_llm_provider()
    fast_model = resolve_model_for_role("comprehension_fast", resolved)
    provider_line = (
        f"your authored text is summarized by {resolved.provider} "
        f"({fast_model}) in batches"
        if resolved.configured
        else "no model key is configured — approving records the intent; "
             "summarization runs once one is added"
    )
    summary = (
        f"i'd read the {count} most recent messages YOU sent — canonical sent "
        "mail, not a label. i keep only what you authored: quoted replies, "
        "forwarded bodies, and signatures are stripped before any model sees "
        f"it. {provider_line}; recipients appear as name/domain only; drafts "
        "and automated mail are excluded. originals stay on this machine as "
        "replay artifacts and every summary cites its message. you can lower "
        "the count, decline, or run it later — nothing is read until you "
        "approve."
    )
    return propose_ingestion_plan_fn(
        graph,
        source_surface_id=source_surface_id,
        service="gmail",
        account_ref=account_ref,
        family="conversation",
        purpose="comprehension",
        window={"kind": "recent_items", "estimated_items": count},
        derivation={
            "basis": "service_default",
            "summary": summary,
            "measurements": {"requested_count": count},
            "provenance": [],
        },
        surfaces=[{
            "surface_ref": "sent",
            "label": "sent mail you authored",
            "included": True,
            "expectation": {
                "estimated_richness": "unmeasured",
                "candidate_types": [
                    "project", "relationship", "responsibility",
                    "preference", "decision",
                ],
            },
        }],
        caps={"max_items": count, "max_pages": max_pages, "page_size": page_size},
        interpretation_stages=[
            "gmail.acquisition@sent",
            "communication.hygiene@authored",
            "subject_synthesis.comprehension@leaves",
            "subject_synthesis.draft@sections",
        ],
        proposed_by=SENT_COMPREHENSION_PROPOSER,
        metadata={
            "comprehension": {
                "recipe_id": SENT_COMPREHENSION_RECIPE_ID,
                "provider_disclosure": {
                    "provider": resolved.provider,
                    "fast_model": fast_model,
                },
                "exclusions": [
                    "drafts", "automated_outbound", "not_owner_authored",
                    "quoted_history", "forwarded_bodies", "signatures",
                ],
                "retention": (
                    "message bodies persist locally as content-addressed "
                    "replay artifacts; summaries cite message evidence"
                ),
            },
        },
        reader=reader,
    )


def _domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else address.lower()


def select_sent_comprehension_items(reader, config: dict[str, Any]) -> dict[str, Any]:
    """The recipe's eligible-item selection: latest-N owner-authored outbound
    conversation messages, exclusions and coverage recorded (ADR 0045 §4).

    Runs over the materialized conversation family, so the deterministic
    hygiene (quoted history, forwards, signatures, notification suppression,
    injection holds) has already produced the bounded authored view.
    """
    surface = str(config.get("source_surface_id") or "")
    max_items = int(config.get("max_items") or DEFAULT_SENT_COUNT)
    excluded: dict[str, int] = {}

    def _exclude(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    eligible = 0
    items: list[dict[str, Any]] = []
    messages = [
        obj for obj in reader.objects(type="conversation_message")
        if obj.data.get("service") == "gmail"
        and (not surface or obj.data.get("source_surface_id") == surface)
    ]
    for message in messages:
        data = message.data or {}
        if data.get("direction") != "outbound":
            continue  # not sent by the owner: outside the approved scope
        eligible += 1
        if data.get("message_kind") in ("notification", "automated"):
            _exclude("automated_outbound")
            continue
        if "DRAFT" in [str(l).upper() for l in data.get("labels") or []]:
            _exclude("draft")
            continue
        if data.get("interpretation_state") == "held":
            _exclude("injection_held")
            continue
        text = str(data.get("interpretation_content") or "").strip()
        if not text:
            _exclude("empty_after_normalization")
            continue
        recipients = [
            {"identity": address.split("@", 1)[0], "domain": _domain_of(address)}
            for address in (data.get("recipients") or [])[:8]
        ]
        items.append({
            "item_ref": message.id,
            "evidence_refs": [str(data.get("evidence_id") or message.id)],
            "subject": data.get("subject") or "",
            "provider_time": data.get("sent_at"),
            "recipients": recipients,
            "thread_ref": data.get("thread_id"),
            "text": text,
            "_sort": str(data.get("sent_at") or ""),
        })
    items.sort(key=lambda item: item["_sort"], reverse=True)
    for item in items:
        item.pop("_sort", None)
    dropped = max(0, len(items) - max_items)
    if dropped:
        excluded["beyond_latest_n"] = dropped
    return {
        "items": items[:max_items],
        "excluded": excluded,
        "coverage": {
            "eligible_outbound": eligible,
            "source": "conversation_family",
            "hygiene": "communication.hygiene@0.1.0",
        },
    }


GMAIL_SENT_RECIPE: dict[str, Any] = {
    "recipe_id": SENT_COMPREHENSION_RECIPE_ID,
    "service": "gmail",
    "family": "conversation",
    "teaches": [
        "projects", "people", "responsibilities", "topics",
        "decisions", "communication_style", "instruction_candidates",
    ],
    "privacy": {
        "excludes": [
            "drafts", "automated_outbound", "not_owner_authored",
            "quoted_history", "forwarded_bodies", "signatures",
            "injection_held",
        ],
        "recipients": "identity and domain only",
        "content": "owner-authored text only, per-item bounded",
    },
    "leaf_schema": [
        "authored_intent", "projects", "people", "responsibilities",
        "topics", "decisions", "communication_style",
        "instruction_candidates", "confidence", "uncertainty",
    ],
    "aggregation": {"group_by": ["projects", "topics"]},
    "batch_size": 10,
    "budgets": {
        "max_items": DEFAULT_SENT_COUNT,
        "max_chars_per_item": 4_000,
        "max_tokens_per_call": 2_000,
        "timeout_seconds_per_call": 120.0,
        "max_synthesis_input_tokens": 12_000,
        "max_aggregation_groups": 6,
    },
    "destinations": [
        "subject_profile", "projects", "entity_relationships",
        "instructions", "information_access_hints",
    ],
    "coverage_required": True,
    "select": select_sent_comprehension_items,
}


def register_gmail_comprehension() -> None:
    register_comprehension_recipe(GMAIL_SENT_RECIPE)


__all__ = [
    "DEFAULT_SENT_COUNT",
    "GMAIL_SENT_RECIPE",
    "SENT_COMPREHENSION_RECIPE_ID",
    "propose_gmail_sent_comprehension_plan_fn",
    "register_gmail_comprehension",
    "select_sent_comprehension_items",
]
