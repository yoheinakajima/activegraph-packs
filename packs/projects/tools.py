"""Project derivation, verdicts, and the neutral projection."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any, Optional

from activegraph.packs import tool


PROJECTS_CONTRACT_VERSION = "projects@0.1.0"
_SEED_FACT_ATTRIBUTES = ("project", "company", "organization", "affiliation")
_ENTITY_TYPES = ("org", "organization", "company", "project", "product")
_GENERIC_LABELS = {
    "inbox", "sent", "draft", "spam", "trash", "important", "starred",
    "unread", "chat", "all mail", "archive",
}


def _stable(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()}"


def _norm(name: str) -> str:
    return " ".join(str(name).split()).casefold()


def derive_project_candidates_fn(
    graph, *, reader=None, limit: int = 12
) -> dict[str, Any]:
    """Deterministic, explainable derivation in seed-priority order (D060).

    1. Owner-confirmed facts naming orgs/projects/affiliations.
    2. Entities recurring in communication (mention frequency; owner-engaged
       threads carry the mentions in the first place).
    3. Presence/research entities from owner-scoped evidence.

    The owner's connector taxonomy (user-created labels) CORROBORATES a
    proposal — a matching label joins its sources and lifts its score —
    but never proposes by itself: labels map how the owner uses a tool,
    not what their world is made of (ADR 0043, amending ADR 0040 §3b).

    Idempotent per name: an existing candidate for the same normalized name
    is refreshed (sources/score merged), never duplicated; confirmed and
    dismissed candidates are left untouched.
    """
    view = reader or graph
    proposals: dict[str, dict[str, Any]] = {}

    def offer(name: str, kind: str, score: int, sources: list[str], rationale: str):
        key = _norm(name)
        if not key or len(key) < 2:
            return
        current = proposals.get(key)
        if current is None:
            proposals[key] = {
                "name": " ".join(str(name).split()),
                "kind": kind,
                "score_milli": min(score, 1_000),
                "sources": list(dict.fromkeys(sources)),
                "rationale": rationale,
            }
            return
        # Higher-priority seeds keep their kind; sources and score merge.
        current["sources"] = list(dict.fromkeys([*current["sources"], *sources]))
        current["score_milli"] = min(1_000, max(current["score_milli"], score) + 50)
        current["rationale"] += f"; {rationale}"

    # 1 — confirmed facts.
    for fact in view.objects(type="subject_fact"):
        data = fact.data or {}
        if data.get("status") != "promoted":
            continue
        if str(data.get("attribute") or "") not in _SEED_FACT_ATTRIBUTES:
            continue
        value = str(data.get("value") or "").strip()
        if value:
            offer(
                value, "fact_seeded", 900, [fact.id],
                f"you confirmed this {data.get('attribute')} yourself",
            )

    # 2 — the owner's own connector taxonomy: collected as corroborating
    # priors, applied after the proposing rungs (ADR 0043).
    labels: dict[str, tuple[str, str]] = {}
    for profile in view.objects(type="integration_profile"):
        if profile.data.get("status") != "active":
            continue
        for container in (profile.data.get("data_topology") or {}).get("containers") or []:
            if str(container.get("type") or "") != "user":
                continue
            name = str(container.get("name") or "").strip()
            leaf = name.split("/")[-1].strip()
            if not leaf or leaf.casefold() in _GENERIC_LABELS:
                continue
            labels.setdefault(_norm(leaf), (profile.id, name))

    # 3 — entities recurring in communication.
    mention_counts: Counter[str] = Counter()
    for mention in view.objects(type="entity_mention"):
        entity_id = mention.data.get("entity_id")
        if entity_id:
            mention_counts[str(entity_id)] += 1
    for entity in view.objects(type="entity"):
        data = entity.data or {}
        if str(data.get("entity_type") or "") not in _ENTITY_TYPES:
            continue
        count = mention_counts.get(entity.id, 0)
        if count < 2:
            continue
        offer(
            str(data.get("name") or ""), "engagement_clustered",
            400 + 50 * min(count, 6), [entity.id],
            f"appears {count}× across your communication",
        )

    # 4 — presence/research entities from owner-scoped evidence.
    for annotation in view.objects(type="semantic_annotation"):
        data = annotation.data or {}
        if data.get("facet") != "entity_mention" or data.get("status") != "active":
            continue
        metadata = data.get("metadata") or {}
        if metadata.get("subject_scope") != "owner_profile":
            continue
        body = data.get("body") or {}
        if str(body.get("kind") or "") not in _ENTITY_TYPES:
            continue
        text = str(body.get("normalized") or body.get("text") or "").strip()
        if text:
            offer(
                text, "presence_clustered", 300, [annotation.id],
                "named in your public presence",
            )

    # Labels corroborate whatever the proposing rungs surfaced: the
    # matching taxonomy ref joins the sources and lifts the score.
    for key, proposal in proposals.items():
        match = labels.get(key)
        if match is None:
            continue
        label_source, label_name = match
        proposal["sources"] = list(dict.fromkeys([*proposal["sources"], label_source]))
        proposal["score_milli"] = min(1_000, proposal["score_milli"] + 100)
        proposal["rationale"] += f"; corroborated by your label '{label_name}'"

    existing_by_name = {
        _norm(obj.data.get("name") or ""): obj
        for obj in view.objects(type="project_candidate")
    }
    confirmed_names = {
        _norm(obj.data.get("name") or "")
        for obj in view.objects(type="project")
        if obj.data.get("status") == "active"
    }

    ranked = sorted(
        proposals.items(),
        key=lambda item: (-item[1]["score_milli"], item[1]["name"].casefold()),
    )[:limit]
    created, refreshed, skipped = 0, 0, 0
    for key, proposal in ranked:
        if key in confirmed_names:
            skipped += 1
            continue
        existing = existing_by_name.get(key)
        if existing is not None:
            if existing.data.get("status") in {"confirmed", "dismissed"}:
                skipped += 1
                continue
            updates = {
                field: proposal[field]
                for field in ("kind", "score_milli", "sources", "rationale")
                if existing.data.get(field) != proposal[field]
            }
            if updates:
                graph.patch_object(existing.id, updates)
                refreshed += 1
            continue
        graph.add_object("project_candidate", {
            "candidate_identity": _stable("project_candidate", key),
            "name": proposal["name"],
            "kind": proposal["kind"],
            "score_milli": proposal["score_milli"],
            "sources": proposal["sources"],
            "rationale": proposal["rationale"],
            "status": "proposed",
            "project_id": None,
            "metadata": {},
        })
        created += 1
    return {
        "ok": True,
        "created": created,
        "refreshed": refreshed,
        "skipped": skipped,
        "proposed_total": len(ranked),
    }


def review_project_candidate_fn(
    graph,
    candidate_id: str,
    verdict: str,
    *,
    actor: str = "owner",
    name_override: Optional[str] = None,
) -> dict[str, Any]:
    """Owner verdict: confirm (optionally renaming) or dismiss."""
    if verdict not in {"confirm", "dismiss"}:
        raise ValueError("verdict must be confirm | dismiss")
    candidate = graph.get_object(candidate_id)
    if candidate is None or candidate.type != "project_candidate":
        raise ValueError(f"unknown project_candidate {candidate_id!r}")
    data = candidate.data or {}
    if data.get("status") != "proposed":
        raise ValueError(f"candidate is {data.get('status')!r}; only proposed candidates take a verdict")
    if verdict == "dismiss":
        graph.patch_object(candidate_id, {"status": "dismissed"},
                           rationale=f"dismissed by {actor}")
        return {"ok": True, "status": "dismissed", "project_id": None}
    name = " ".join(str(name_override or data.get("name") or "").split())
    if not name:
        raise ValueError("a confirmed project needs a name")
    project = graph.add_object("project", {
        "project_identity": _stable("project", _norm(name)),
        "name": name,
        "status": "active",
        "seeded_from_candidate_id": candidate_id,
        "confirmed_by": actor,
        "supersedes": None,
        "superseded_by": None,
        "metadata": {
            "sources": list(data.get("sources") or []),
            "derivation_kind": data.get("kind"),
            **({"renamed_from": data.get("name")} if name_override else {}),
        },
    })
    graph.patch_object(candidate_id, {"status": "confirmed", "project_id": project.id},
                       rationale=f"confirmed by {actor}")
    return {"ok": True, "status": "confirmed", "project_id": project.id}


def rename_project_fn(graph, project_id: str, name: str, *, actor: str = "owner") -> dict[str, Any]:
    """Rename is supersession (ADR 0020): a new version replaces the old."""
    project = graph.get_object(project_id)
    if project is None or project.type != "project":
        raise ValueError(f"unknown project {project_id!r}")
    if project.data.get("status") != "active":
        raise ValueError("only an active project can be renamed")
    name = " ".join(str(name).split())
    if not name:
        raise ValueError("a project needs a name")
    replacement = graph.add_object("project", {
        "project_identity": _stable("project", _norm(name), project.id),
        "name": name,
        "status": "active",
        "seeded_from_candidate_id": project.data.get("seeded_from_candidate_id"),
        "confirmed_by": actor,
        "supersedes": project.id,
        "superseded_by": None,
        "metadata": dict(project.data.get("metadata") or {}),
    })
    graph.patch_object(project_id, {"status": "superseded", "superseded_by": replacement.id},
                       rationale=f"renamed by {actor}")
    return {"ok": True, "project_id": replacement.id, "superseded": project_id}


def project_projects_fn(graph) -> dict[str, Any]:
    candidates = sorted(
        (dict(obj.data) | {"candidate_object_id": obj.id}
         for obj in graph.objects(type="project_candidate")),
        key=lambda row: (-int(row.get("score_milli") or 0), str(row.get("name") or "").casefold()),
    )
    projects = sorted(
        (dict(obj.data) | {"project_object_id": obj.id}
         for obj in graph.objects(type="project")),
        key=lambda row: str(row.get("name") or "").casefold(),
    )
    return {
        "contract_version": PROJECTS_CONTRACT_VERSION,
        "candidates": candidates,
        "projects": projects,
    }


@tool(name="derive_project_candidates", description="Derive explainable project candidates from confirmed facts, owner taxonomy, and evidence.")
def derive_project_candidates(graph, limit: int = 12):
    return derive_project_candidates_fn(graph, limit=limit)


@tool(name="review_project_candidate", description="Confirm (optionally rename) or dismiss a proposed project.")
def review_project_candidate(graph, candidate_id: str = "", verdict: str = "confirm", name_override: str = "", actor: str = "owner"):
    return review_project_candidate_fn(graph, candidate_id, verdict, actor=actor, name_override=name_override or None)


@tool(name="rename_project", description="Rename an active project through supersession.")
def rename_project(graph, project_id: str = "", name: str = "", actor: str = "owner"):
    return rename_project_fn(graph, project_id, name, actor=actor)


@tool(name="project_projects", description="Project every project candidate and confirmed project.")
def project_projects(graph, _ctx=None):
    return project_projects_fn(graph)


TOOLS = [derive_project_candidates, review_project_candidate, rename_project, project_projects]

__all__ = [
    "PROJECTS_CONTRACT_VERSION",
    "TOOLS",
    "derive_project_candidates_fn",
    "project_projects_fn",
    "rename_project_fn",
    "review_project_candidate_fn",
]
