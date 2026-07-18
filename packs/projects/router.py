"""The evidence→workstream router (ADR 0049 §4; MOCK_REFERENCE GAP 0's
routing half).

Deterministic, explainable, bounded. Every score is a sum of named signals
over typed graph state — entity associations, confirmed aliases, the
owner's own labels, description overlap, and recorded owner corrections.
No model output participates in the decision; an LLM may annotate evidence
upstream, never file it.

Uncertainty behavior is doctrine, not tuning: a confident single winner
routes automatically (reversible, receipted, correctable); anything
ambiguous stays honestly unfiled. Nothing is ever force-filed, and an
owner's unroute correction pins an item unfiled until the owner says
otherwise.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .graph import route_item_fn

ROUTER_POLICY = "projects.router.evidence-signals@0.1.0"

#: Item families the router sweeps by default: the human-readable spine
#: (threads and comprehension summaries). Finer-grained families
#: (activity_evidence, conversation_message, subject_fact) remain legal
#: `routed_to` sources for explicit/manual routing but are not swept —
#: an Unfiled tray full of opaque revision ids would be noise, not truth.
ROUTABLE_FAMILIES = (
    "conversation_thread",
    "source_item_summary",
)

AUTO_ROUTE_THRESHOLD_MILLI = 500
AUTO_ROUTE_MARGIN_MILLI = 150

_SIGNAL_WEIGHTS = {
    "association": 350,       # per associated participant entity (capped x2)
    "alias_mention": 500,     # the FULL project/entity/alias name in the
                              # item text — precise, so decisive when unique
    "name_token": 400,        # a name token ONLY this project uses
                              # ("untapped" when no other project has it)
    "sender_domain": 300,     # participant address domain carries an
                              # associated org entity's name
    "label_match": 250,       # the owner's own connector label agrees
    "description_overlap": 60,  # per shared meaningful token (capped x3)
    "correction_toward": 250,  # owner corrections teach the router
    "correction_away": -300,
}

_STOP_TOKENS = {
    "with", "from", "this", "that", "then", "them", "your", "have", "will",
    "about", "into", "over", "under", "just", "very", "when", "where",
    "what", "which", "their", "there", "these", "those", "some", "more",
    "management", "support", "relations", "general", "misc", "other",
}

#: Words common in organization names that identify nothing by themselves:
#: "645 Ventures" must never route to "Scrum Ventures" on "ventures".
#: They still count inside FULL-name mentions; they never count as
#: distinctive tokens or domain matches.
_CORPORATE_GENERIC = {
    "ventures", "venture", "capital", "partners", "partner", "holdings",
    "group", "labs", "fund", "funds", "company", "companies",
    "technologies", "technology", "international", "global", "solutions",
    "studio", "studios", "collective", "media", "digital", "network",
}


def _tokens(text: str) -> set[str]:
    return {
        token for token in re.split(r"[^a-z0-9]+", str(text).casefold())
        if len(token) >= 4 and token not in _STOP_TOKENS
    }


def _word_mention(needle: str, haystack: str) -> bool:
    needle = " ".join(str(needle).split()).casefold()
    if len(needle) < 3:
        return False
    return re.search(
        rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", str(haystack).casefold(),
    ) is not None


def _active_routes(reader, item_ref: str) -> list[Any]:
    return [
        relation for relation in reader.relations(source=item_ref, type="routed_to")
        if not (relation.data or {}).get("removed")
    ]


def _item_text(item) -> str:
    data = getattr(item, "data", None) or {}
    return " ".join(
        str(data.get(field) or "")
        for field in ("subject", "title", "summary", "text", "name")
    )


def _participant_entities(reader, item) -> list[str]:
    """Non-owner participant entity ids for a conversation thread; empty
    for other families (their refs may still carry entity ids)."""
    data = getattr(item, "data", None) or {}
    out: list[str] = []
    for participant_id in data.get("participant_ids") or []:
        participant = reader.get_object(participant_id)
        pdata = getattr(participant, "data", None) or {}
        if pdata.get("is_owner"):
            continue
        entity_id = pdata.get("entity_id")
        if entity_id:
            out.append(str(entity_id))
    for ref in data.get("refs") or []:
        if str(ref).startswith("entity"):
            out.append(str(ref))
    return list(dict.fromkeys(out))


def _participant_domains(reader, item) -> set[str]:
    """First labels of non-owner participant address domains
    ("maggie@untapped.vc" -> "untapped")."""
    data = getattr(item, "data", None) or {}
    domains: set[str] = set()
    for participant_id in data.get("participant_ids") or []:
        participant = reader.get_object(participant_id)
        pdata = getattr(participant, "data", None) or {}
        if pdata.get("is_owner"):
            continue
        address = str(pdata.get("address") or "")
        if "@" in address:
            domain = address.rsplit("@", 1)[1].casefold()
            label = domain.split(".", 1)[0]
            if len(label) >= 4:
                domains.add(label)
    return domains


def _project_rows(reader) -> list[Any]:
    return [
        obj for obj in reader.objects(type="project")
        if (obj.data or {}).get("status") == "active"
    ]


def _associated_entities(reader, project_id: str) -> dict[str, str]:
    """entity_id -> relation id, for evidence refs."""
    out: dict[str, str] = {}
    for relation in reader.relations(
        source=project_id, type="workstream_associated_with",
    ):
        if (relation.data or {}).get("removed"):
            continue
        out[str(relation.target)] = relation.id
    return out


def _confirmed_aliases(reader, association_names: set[str]) -> list[str]:
    aliases: list[str] = []
    for fact in reader.objects(type="subject_fact"):
        data = fact.data or {}
        if data.get("status") != "promoted":
            continue
        value = str(data.get("value") or "")
        if value and value.casefold() in association_names:
            aliases.append(value)
    return aliases


def _correction_pinned_unfiled(reader, item_ref: str) -> bool:
    """An owner unroute with no later re-route pins the item unfiled."""
    last: Optional[Any] = None
    for correction in reader.objects(type="routing_correction"):
        data = correction.data or {}
        if data.get("item_ref") != item_ref:
            continue
        last = correction
    if last is None:
        return False
    return (last.data or {}).get("kind") == "unroute"


def _correction_signals(reader, item_entities: set[str], labels: set[str]):
    """Owner corrections whose subject item shared a participant entity or
    an owner label with this item teach the router toward/away."""
    toward: dict[str, list[str]] = {}
    away: dict[str, list[str]] = {}
    for correction in reader.objects(type="routing_correction"):
        data = correction.data or {}
        corrected_item = reader.get_object(str(data.get("item_ref") or ""))
        if corrected_item is None:
            continue
        cdata = getattr(corrected_item, "data", None) or {}
        shared_entity = bool(
            item_entities
            and item_entities.intersection(
                _participant_entities(reader, corrected_item))
        )
        shared_label = bool(
            labels
            and labels.intersection(
                str(label).casefold() for label in cdata.get("labels") or [])
        )
        if not (shared_entity or shared_label):
            continue
        if data.get("to_project_id"):
            toward.setdefault(str(data["to_project_id"]), []).append(correction.id)
        if data.get("from_project_id"):
            away.setdefault(str(data["from_project_id"]), []).append(correction.id)
    return toward, away


def derive_route_fn(reader, item_ref: str) -> dict[str, Any]:
    """Score every active workstream for one item and decide:
    ``route`` (one confident winner), ``unfiled`` (honest ambiguity or no
    signal), ``already_routed``, or ``owner_unfiled`` (a standing owner
    correction). Every candidate lists the exact signals that scored."""
    item = reader.get_object(item_ref)
    if item is None:
        return {"ok": False, "reason": "unknown_item", "item_ref": item_ref}
    if _active_routes(reader, item_ref):
        return {"ok": True, "decision": "already_routed", "item_ref": item_ref}
    if _correction_pinned_unfiled(reader, item_ref):
        return {"ok": True, "decision": "owner_unfiled", "item_ref": item_ref,
                "explanation": "you un-filed this; it stays put until you re-file it"}

    text = _item_text(item)
    item_entities = set(_participant_entities(reader, item))
    labels = {
        str(label).casefold()
        for label in (getattr(item, "data", None) or {}).get("labels") or []
    }
    toward, away = _correction_signals(reader, item_entities, labels)

    # Distinctive name tokens: a token (len >= 5) that exactly one active
    # project's name/associated-entity names use — "untapped" identifies
    # Untapped Capital; "capital" (shared with another workstream) never
    # scores for anyone.
    projects = _project_rows(reader)
    tokens_by_project: dict[str, set[str]] = {}
    for project in projects:
        token_set = _tokens(str((project.data or {}).get("name") or ""))
        for entity_id in _associated_entities(reader, project.id):
            entity = reader.get_object(entity_id)
            token_set |= _tokens(
                str((getattr(entity, "data", None) or {}).get("name") or ""))
        tokens_by_project[project.id] = {
            token for token in token_set
            if len(token) >= 5 and token not in _CORPORATE_GENERIC
        }
    token_owners: dict[str, int] = {}
    for token_set in tokens_by_project.values():
        for token in token_set:
            token_owners[token] = token_owners.get(token, 0) + 1
    distinctive_by_project = {
        project_id: {
            token for token in token_set if token_owners.get(token) == 1
        }
        for project_id, token_set in tokens_by_project.items()
    }

    candidates: list[dict[str, Any]] = []
    for project in projects:
        project_id = project.id
        name = str((project.data or {}).get("name") or "")
        description = str((project.data or {}).get("description") or "")
        associations = _associated_entities(reader, project_id)
        association_names = set()
        for entity_id in associations:
            entity = reader.get_object(entity_id)
            entity_name = str((getattr(entity, "data", None) or {}).get("name") or "")
            if entity_name:
                association_names.add(entity_name.casefold())

        signals: dict[str, int] = {}
        evidence: list[str] = []

        matched = item_entities.intersection(associations)
        if matched:
            signals["association"] = _SIGNAL_WEIGHTS["association"] * min(len(matched), 2)
            evidence.extend(sorted(matched)[:2])
            evidence.extend(associations[entity_id] for entity_id in sorted(matched)[:2])

        entity_aliases: list[str] = []
        for entity_id in associations:
            entity = reader.get_object(entity_id)
            for alias in (getattr(entity, "data", None) or {}).get("aliases") or []:
                entity_aliases.append(str(alias))
        mention_names = [name, *_confirmed_aliases(reader, association_names)]
        mention_names.extend(sorted(association_names))
        mention_names.extend(entity_aliases)
        if any(_word_mention(candidate_name, text)
               for candidate_name in mention_names if candidate_name):
            signals["alias_mention"] = _SIGNAL_WEIGHTS["alias_mention"]
        elif any(
            _word_mention(token, text)
            for token in distinctive_by_project.get(project_id, ())
        ):
            signals["name_token"] = _SIGNAL_WEIGHTS["name_token"]

        domains = _participant_domains(reader, item)
        if domains:
            org_tokens = set()
            for candidate_name in (name, *sorted(association_names)):
                org_tokens.update(_tokens(candidate_name))
            org_tokens -= _CORPORATE_GENERIC
            if any(
                token in label or label in token
                for token in org_tokens if len(token) >= 5
                for label in domains if label not in _CORPORATE_GENERIC
            ):
                signals["sender_domain"] = _SIGNAL_WEIGHTS["sender_domain"]

        name_tokens = _tokens(name)
        if labels and any(
            _tokens(label) & name_tokens or label == name.casefold()
            for label in labels
        ):
            signals["label_match"] = _SIGNAL_WEIGHTS["label_match"]

        overlap = _tokens(description) & _tokens(text)
        if overlap:
            signals["description_overlap"] = (
                _SIGNAL_WEIGHTS["description_overlap"] * min(len(overlap), 3)
            )

        if project_id in toward:
            signals["correction_toward"] = _SIGNAL_WEIGHTS["correction_toward"]
            evidence.extend(toward[project_id][:2])
        if project_id in away:
            signals["correction_away"] = _SIGNAL_WEIGHTS["correction_away"]
            evidence.extend(away[project_id][:2])

        score = max(0, min(1000, sum(signals.values())))
        if score > 0:
            candidates.append({
                "project_id": project_id,
                "name": name,
                "score_milli": score,
                "signals": signals,
                "evidence_refs": list(dict.fromkeys(evidence))[:6],
            })

    candidates.sort(key=lambda row: (-row["score_milli"], row["name"].casefold()))
    top = candidates[0] if candidates else None
    runner_up = candidates[1]["score_milli"] if len(candidates) > 1 else 0
    confident = (
        top is not None
        and top["score_milli"] >= AUTO_ROUTE_THRESHOLD_MILLI
        and top["score_milli"] - runner_up >= AUTO_ROUTE_MARGIN_MILLI
    )
    return {
        "ok": True,
        "item_ref": item_ref,
        "item_type": getattr(item, "type", None),
        "decision": "route" if confident else "unfiled",
        "candidates": candidates[:5],
        "policy": {
            "id": ROUTER_POLICY,
            "threshold_milli": AUTO_ROUTE_THRESHOLD_MILLI,
            "margin_milli": AUTO_ROUTE_MARGIN_MILLI,
        },
    }


def _provenance_line(top: dict[str, Any]) -> str:
    named = {
        "association": "shared people",
        "alias_mention": "named in the item",
        "name_token": "a name only this project uses appears",
        "sender_domain": "sender's company matches",
        "label_match": "your label agrees",
        "description_overlap": "matches the project description",
        "correction_toward": "you filed similar items here",
        "correction_away": "you moved similar items away",
    }
    parts = [named[key] for key in top["signals"] if key in named]
    return f"router: {', '.join(parts)} ({top['score_milli']}/1000)"


def bootstrap_associations_fn(
    graph, *, reader=None, actor: str = "agent:router",
) -> dict[str, Any]:
    """Derive the obvious missing workstream↔entity associations once:
    an entity whose normalized name equals an active workstream's name (or
    one of its confirmed aliases) is associated with role ``named_match``
    and that provenance. The string match happens HERE, at derivation,
    with a receipt — context selection and routing stay typed reachability
    (ADR 0049 §4). Idempotent; the owner can dissociate."""
    from .graph import associate_workstream_fn

    view = reader or graph
    def _norm(text: str) -> str:
        return " ".join(str(text).split()).casefold()

    alias_values = {
        _norm(fact.data.get("value") or "")
        for fact in view.objects(type="subject_fact")
        if (fact.data or {}).get("status") == "promoted"
    }
    alias_values.discard("")
    created = []
    for project in _project_rows(view):
        wanted = {_norm((project.data or {}).get("name") or "")}
        wanted.discard("")
        for entity in view.objects(type="entity"):
            name = _norm((entity.data or {}).get("name") or "")
            if not name:
                continue
            if name in wanted or (name in alias_values and name in wanted):
                result = associate_workstream_fn(
                    graph, project.id, entity.id, role="named_match",
                    actor=actor, reader=view,
                )
                if result.get("ok") and not result.get("already_associated"):
                    created.append({
                        "project_id": project.id, "entity_id": entity.id,
                    })
    return {"ok": True, "created": created}


def unrouted_items_fn(
    reader, *, families: tuple[str, ...] = ROUTABLE_FAMILIES, limit: int = 50,
) -> dict[str, Any]:
    """The honest Unfiled tray: routeable items with no active route and no
    standing owner unroute, newest first, with a bounded total count."""
    rows: list[dict[str, Any]] = []
    total = 0
    for family in families:
        for item in reader.objects(type=family):
            if _active_routes(reader, item.id):
                continue
            total += 1
            rows.append({
                "item_ref": item.id,
                "item_type": family,
                "label": _item_text(item).strip()[:120] or item.id,
                "owner_unfiled": _correction_pinned_unfiled(reader, item.id),
            })
    rows.reverse()  # object iteration is insertion order; newest first
    return {"total": total, "items": rows[:limit]}


def route_pending_fn(
    graph, *, reader=None,
    families: tuple[str, ...] = ROUTABLE_FAMILIES,
    limit: int = 25, actor: str = "agent:router",
) -> dict[str, Any]:
    """One bounded routing pass: derive a decision for up to ``limit``
    unrouted items and file only the confident ones (with the signal
    summary as provenance and the scoring evidence attached). Ambiguity
    stays unfiled; re-running is idempotent; replay is stable because the
    decision is a pure function of graph state."""
    view = reader or graph
    routed: list[dict[str, Any]] = []
    examined = 0
    unfiled = 0
    for family in families:
        for item in view.objects(type=family):
            if examined >= limit:
                break
            if _active_routes(view, item.id):
                continue
            examined += 1
            derived = derive_route_fn(view, item.id)
            if derived.get("decision") != "route":
                if derived.get("decision") in ("unfiled", "owner_unfiled"):
                    unfiled += 1
                continue
            top = derived["candidates"][0]
            result = route_item_fn(
                graph, item.id, top["project_id"], actor=actor,
                provenance=_provenance_line(top),
                evidence_refs=top["evidence_refs"],
                confidence_milli=top["score_milli"],
                reader=view,
            )
            if result.get("ok"):
                routed.append({
                    "item_ref": item.id,
                    "project_id": top["project_id"],
                    "score_milli": top["score_milli"],
                    "signals": top["signals"],
                })
        if examined >= limit:
            break
    return {
        "ok": True,
        "examined": examined,
        "routed": routed,
        "unfiled": unfiled,
        "policy_id": ROUTER_POLICY,
    }


__all__ = [
    "AUTO_ROUTE_MARGIN_MILLI",
    "AUTO_ROUTE_THRESHOLD_MILLI",
    "ROUTABLE_FAMILIES",
    "ROUTER_POLICY",
    "bootstrap_associations_fn",
    "derive_route_fn",
    "route_pending_fn",
    "unrouted_items_fn",
]
