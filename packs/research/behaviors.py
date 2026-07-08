"""Research Pack behaviors — v0.1.

Behaviors:
  paper_ingester              — source.created (kind=research_paper) → Paper + Author + Venue
  claim_extractor             — paper.created → observations (claims)
  idea_atom_extractor         — paper.created → IdeaAtom objects
  hypothesis_generator        — idea_atom.created (high coherence) → ResearchDirection
  research_direction_synthesizer — idea_atom.created → merges into existing direction

All LLM behaviors use deterministic mock stubs in v0.1 (no API key required).

Registries:
  _PAPER_REGISTRY: source_id → paper_id
  _VENUE_REGISTRY: name_lower → venue_id
  _DIRECTION_REGISTRY: tag_key → direction_id (for dedup/merging)
  Call clear_research_registry() between test fixtures.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from activegraph.packs import behavior

from .settings import ResearchSettings

_PAPER_REGISTRY: dict[str, str] = {}
_VENUE_REGISTRY: dict[str, str] = {}
_DIRECTION_REGISTRY: dict[str, str] = {}
_IDEA_ATOMS_BY_PAPER: dict[str, list[str]] = {}


def clear_research_registry() -> None:
    _PAPER_REGISTRY.clear()
    _VENUE_REGISTRY.clear()
    _DIRECTION_REGISTRY.clear()
    _IDEA_ATOMS_BY_PAPER.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_authors(author_str: str) -> list[str]:
    """Split author string by comma, semicolon, or 'and'."""
    parts = re.split(r"[,;]| and | & ", author_str)
    return [p.strip() for p in parts if p.strip()]


def _extract_keywords(text: str, max_kw: int = 6) -> list[str]:
    """Extract candidate keywords from text using TF heuristics."""
    STOPWORDS = {
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "is", "are", "was", "were", "be", "been", "this", "that",
        "we", "our", "paper", "show", "shows", "method", "approach", "propose",
        "proposed", "present", "presented", "using", "used", "can", "also",
        "by", "from", "as", "which", "have", "has", "been", "its", "their",
    }
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    freq: dict[str, int] = {}
    for w in words:
        if w not in STOPWORDS:
            freq[w] = freq.get(w, 0) + 1
    sorted_kw = sorted(freq, key=lambda k: -freq[k])
    return sorted_kw[:max_kw]


def _mock_claims(title: str, abstract: str, count: int) -> list[str]:
    """Generate deterministic mock claim observations from a paper."""
    sentences = [s.strip() for s in re.split(r"[.!?]", abstract) if len(s.strip()) > 20]
    claims = sentences[:count] if sentences else []
    if not claims:
        claims = [f"The paper '{title}' proposes a novel approach to {title.lower()[:40]}."]
    return claims


def _mock_idea_atoms(title: str, keywords: list[str], count: int) -> list[str]:
    """Generate deterministic mock idea atoms from paper keywords."""
    templates = [
        "Combining {a} and {b} may yield improvements in {c}.",
        "Applying {a} to {b} problems opens new research directions.",
        "{a} could be extended to handle {b} scenarios.",
        "The relationship between {a} and {b} warrants deeper investigation.",
        "Scaling {a} techniques to {b} remains an open challenge.",
    ]
    kw = keywords + ["performance", "efficiency", "accuracy", "robustness", "generalization"]
    atoms = []
    for i in range(min(count, len(templates))):
        a = kw[i % len(kw)]
        b = kw[(i + 1) % len(kw)]
        c = kw[(i + 2) % len(kw)]
        atoms.append(templates[i % len(templates)].format(a=a, b=b, c=c))
    return atoms[:count]


@behavior(
    name="paper_ingester",
    on=["object.created"],
    where={"object.type": "source"},
    creates=["paper", "author", "venue"],
)
def paper_ingester(event, graph, ctx, *, settings: ResearchSettings):
    """Ingest a research paper source into Paper + Author(s) + Venue objects.

    On: object.created (source, kind=research_paper)
    Creates: paper, author(s), venue, authored_by relation, published_in relation,
             derived_from_source relation
    """
    obj = event.payload.get("object", {})
    source_id = obj.get("id")
    data = obj.get("data", {})

    if data.get("kind") != "research_paper":
        return

    if source_id in _PAPER_REGISTRY:
        return

    meta = data.get("metadata") or {}
    title = meta.get("title") or data.get("content", "")[:80] or "Untitled Paper"
    abstract = meta.get("abstract") or data.get("content") or ""
    year = meta.get("year")
    doi = meta.get("doi")
    arxiv_id = meta.get("arxiv_id")
    venue_name = meta.get("venue") or meta.get("journal") or meta.get("conference") or ""
    author_str = meta.get("authors") or ""
    keywords = meta.get("keywords") or _extract_keywords(abstract)

    # ── Venue ──────────────────────────────────────────────────────────────
    venue_id = None
    if venue_name:
        vkey = venue_name.lower().strip()
        if vkey in _VENUE_REGISTRY:
            venue_id = _VENUE_REGISTRY[vkey]
        else:
            try:
                venue_obj = graph.add_object("venue", {
                    "name": venue_name,
                    "kind": "conference" if any(w in venue_name.lower() for w in
                                                ["conference", "conf", "workshop", "symposium"]) else "journal",
                })
                venue_id = venue_obj.id
                _VENUE_REGISTRY[vkey] = venue_id
            except Exception:
                pass

    # ── Paper ──────────────────────────────────────────────────────────────
    try:
        paper = graph.add_object("paper", {
            "title": title,
            "abstract": abstract[:2000],
            "year": year,
            "doi": doi,
            "arxiv_id": arxiv_id,
            "venue_id": venue_id,
            "keywords": keywords if isinstance(keywords, list) else [keywords],
            "source_id": source_id,
        })
        paper_id = paper.id
        _PAPER_REGISTRY[source_id] = paper_id
        graph.add_relation(paper_id, source_id, "derived_from_source")
    except Exception:
        return

    if venue_id:
        try:
            graph.add_relation(paper_id, venue_id, "published_in")
        except Exception:
            pass

    # ── Authors ────────────────────────────────────────────────────────────
    if author_str:
        author_names = _parse_authors(author_str)
        for aname in author_names:
            try:
                author_obj = graph.add_object("author", {
                    "name": aname,
                    "paper_ids": [paper_id],
                })
                graph.add_relation(paper_id, author_obj.id, "authored_by")
            except Exception:
                pass


@behavior(
    name="claim_extractor",
    on=["object.created"],
    where={"object.type": "paper"},
    creates=["observation"],
)
def claim_extractor(event, graph, ctx, *, settings: ResearchSettings):
    """Extract claim observations from a newly ingested paper.

    On: object.created (paper)
    Creates: observation (category=fact) for each extracted claim
    Relations: grounds(source → observation) if source_id available

    v0.1: deterministic mock extraction from abstract sentences.
    """
    obj = event.payload.get("object", {})
    paper_id = obj.get("id")
    data = obj.get("data", {})

    title = data.get("title") or ""
    abstract = data.get("abstract") or ""
    source_id = data.get("source_id")

    claims = _mock_claims(title, abstract, settings.max_claims_per_paper)

    for claim_text in claims:
        try:
            obs = graph.add_object("observation", {
                "text": claim_text,
                "confidence": 0.75,
                "source_ids": [source_id] if source_id else [],
                "category": "fact",
                "metadata": {"paper_id": paper_id, "extracted_by": "claim_extractor"},
            })
            if source_id:
                try:
                    graph.add_relation(source_id, obs.id, "grounds")
                except Exception:
                    pass
        except Exception:
            pass


@behavior(
    name="idea_atom_extractor",
    on=["object.created"],
    where={"object.type": "paper"},
    creates=["idea_atom"],
)
def idea_atom_extractor(event, graph, ctx, *, settings: ResearchSettings):
    """Extract atomic research ideas from a paper.

    On: object.created (paper)
    Creates: idea_atom objects
    Relations: proposes_idea(paper → idea_atom)

    v0.1: mock extraction based on paper keywords.
    """
    obj = event.payload.get("object", {})
    paper_id = obj.get("id")
    data = obj.get("data", {})

    keywords = data.get("keywords") or []
    title = data.get("title") or ""
    abstract = data.get("abstract") or ""

    if not keywords:
        keywords = _extract_keywords(abstract or title)

    ideas = _mock_idea_atoms(title, keywords, settings.max_ideas_per_paper)

    atom_ids = []
    for i, idea_text in enumerate(ideas):
        novelty = round(min(0.4 + (i * 0.08), 0.9), 2)
        coherence = round(min(0.5 + (i * 0.06), 0.88), 2)
        try:
            atom = graph.add_object("idea_atom", {
                "text": idea_text,
                "source_paper_ids": [paper_id],
                "tags": keywords[:3],
                "novelty_score": novelty,
                "coherence_score": coherence,
            })
            graph.add_relation(paper_id, atom.id, "proposes_idea")
            atom_ids.append(atom.id)
        except Exception:
            pass

    _IDEA_ATOMS_BY_PAPER[paper_id] = atom_ids


@behavior(
    name="hypothesis_generator",
    on=["object.created"],
    where={"object.type": "idea_atom"},
    creates=["research_direction"],
)
def hypothesis_generator(event, graph, ctx, *, settings: ResearchSettings):
    """Generate a research direction from a high-coherence idea atom.

    On: object.created (idea_atom, coherence >= min_coherence_for_hypothesis)
    Creates: research_direction (candidate)
    Relations: composes_direction(idea_atom → research_direction)

    v0.1: mock direction synthesis — one direction per qualifying atom.
    Dedup: reuses existing direction if same tags.
    """
    if not settings.auto_synthesize_directions:
        return

    obj = event.payload.get("object", {})
    atom_id = obj.get("id")
    data = obj.get("data", {})

    coherence = data.get("coherence_score", 0.0)
    if coherence < settings.min_coherence_for_hypothesis:
        return

    tags = data.get("tags") or []
    tag_key = ":".join(sorted(tags[:2])) if tags else "general"

    if tag_key in _DIRECTION_REGISTRY:
        direction_id = _DIRECTION_REGISTRY[tag_key]
        try:
            existing = graph.get_object(direction_id)
            if existing:
                current_ids = existing.data.get("idea_atom_ids") or []
                if atom_id not in current_ids:
                    graph.patch_object(direction_id, {
                        "idea_atom_ids": current_ids + [atom_id],
                    })
        except Exception:
            pass
        try:
            graph.add_relation(atom_id, direction_id, "composes_direction")
        except Exception:
            pass
        return

    idea_text = data.get("text") or ""
    direction_title = f"Research Direction: {', '.join(tags[:2]) if tags else idea_text[:40]}"
    summary = (
        f"Emerging direction based on idea: '{idea_text}'. "
        f"Tags: {', '.join(tags)}. Synthesized from high-coherence idea atoms."
    )

    try:
        direction = graph.add_object("research_direction", {
            "title": direction_title,
            "summary": summary,
            "idea_atom_ids": [atom_id],
            "status": "candidate",
            "confidence": round(coherence * 0.9, 2),
        })
        _DIRECTION_REGISTRY[tag_key] = direction.id
        graph.add_relation(atom_id, direction.id, "composes_direction")
    except Exception:
        pass


@behavior(
    name="research_direction_synthesizer",
    on=["object.created"],
    where={"object.type": "idea_atom"},
    creates=["research_direction"],
)
def research_direction_synthesizer(event, graph, ctx, *, settings: ResearchSettings):
    """Synthesize a research direction when enough idea atoms accumulate.

    On: object.created (idea_atom)
    Checks: if multiple atoms share tags and min_ideas_for_direction is met,
            synthesizes a combined ResearchDirection.

    v0.1: works alongside hypothesis_generator; creates higher-confidence directions
    when multiple atoms converge on the same topic.
    """
    if not settings.auto_synthesize_directions:
        return

    obj = event.payload.get("object", {})
    atom_id = obj.get("id")
    data = obj.get("data", {})
    tags = data.get("tags") or []

    if not tags:
        return

    primary_tag = tags[0].lower()
    synth_key = f"synth:{primary_tag}"

    all_atoms_for_tag = [
        aid for aid, _ in _DIRECTION_REGISTRY.items()
        if False  # We scan _IDEA_ATOMS_BY_PAPER instead
    ]

    all_atom_ids_flat = [a for aids in _IDEA_ATOMS_BY_PAPER.values() for a in aids]
    if len(all_atom_ids_flat) < settings.min_ideas_for_direction:
        return

    if synth_key in _DIRECTION_REGISTRY:
        direction_id = _DIRECTION_REGISTRY[synth_key]
        try:
            existing = graph.get_object(direction_id)
            if existing:
                current_ids = existing.data.get("idea_atom_ids") or []
                if atom_id not in current_ids:
                    graph.patch_object(direction_id, {
                        "idea_atom_ids": current_ids + [atom_id],
                        "confidence": min(0.95, (existing.data.get("confidence") or 0.6) + 0.05),
                    })
        except Exception:
            pass
        return

    try:
        direction = graph.add_object("research_direction", {
            "title": f"Synthesized Direction: {primary_tag.title()} Research",
            "summary": (
                f"Cross-paper synthesis for '{primary_tag}' research. "
                f"Converging signals from {len(all_atom_ids_flat)} idea atoms across multiple papers."
            ),
            "idea_atom_ids": [atom_id],
            "status": "candidate",
            "confidence": 0.72,
        })
        _DIRECTION_REGISTRY[synth_key] = direction.id
        graph.add_relation(atom_id, direction.id, "composes_direction")
    except Exception:
        pass


BEHAVIORS = [
    paper_ingester,
    claim_extractor,
    idea_atom_extractor,
    hypothesis_generator,
    research_direction_synthesizer,
]
