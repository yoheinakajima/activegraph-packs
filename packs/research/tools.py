"""Research Pack tools — v0.1."""

from __future__ import annotations

from activegraph import Graph
from activegraph.packs import tool


def ingest_research_paper_fn(
    graph: Graph,
    title: str,
    abstract: str = "",
    authors: str = "",
    venue: str = "",
    year: int | None = None,
    doi: str | None = None,
    arxiv_id: str | None = None,
    keywords: list[str] | None = None,
) -> object:
    """Create a source object for a research paper, triggering paper_ingester."""
    return graph.add_object("source", {
        "kind": "research_paper",
        "content": abstract,
        "channel": "research",
        "metadata": {
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "venue": venue,
            "year": year,
            "doi": doi,
            "arxiv_id": arxiv_id,
            "keywords": keywords or [],
        },
    })


def create_idea_atom_fn(
    graph: Graph,
    text: str,
    source_paper_ids: list[str] | None = None,
    tags: list[str] | None = None,
    novelty_score: float = 0.6,
    coherence_score: float = 0.6,
) -> object:
    """Directly create an IdeaAtom, triggering hypothesis_generator and synthesizer."""
    return graph.add_object("idea_atom", {
        "text": text,
        "source_paper_ids": source_paper_ids or [],
        "tags": tags or [],
        "novelty_score": novelty_score,
        "coherence_score": coherence_score,
    })


def create_experiment_fn(
    graph: Graph,
    title: str,
    hypothesis: str,
    direction_id: str | None = None,
) -> object:
    """Create a research Experiment linked to a direction."""
    exp = graph.add_object("experiment", {
        "title": title,
        "hypothesis": hypothesis,
        "direction_id": direction_id,
        "status": "proposed",
    })
    if direction_id:
        try:
            graph.add_relation(exp.id, direction_id, "tests_direction")
        except Exception:
            pass
    return exp


@tool(name="ingest_research_paper", description="Ingest a research paper into the graph.")
def ingest_research_paper(
    graph: Graph,
    title: str,
    abstract: str = "",
    authors: str = "",
    venue: str = "",
    year: int | None = None,
    doi: str | None = None,
    arxiv_id: str | None = None,
    keywords: list[str] | None = None,
) -> object:
    return ingest_research_paper_fn(graph, title, abstract, authors, venue, year, doi, arxiv_id, keywords)


@tool(name="create_idea_atom", description="Create an IdeaAtom from a research insight.")
def create_idea_atom(
    graph: Graph,
    text: str,
    source_paper_ids: list[str] | None = None,
    tags: list[str] | None = None,
    novelty_score: float = 0.6,
    coherence_score: float = 0.6,
) -> object:
    return create_idea_atom_fn(graph, text, source_paper_ids, tags, novelty_score, coherence_score)


@tool(name="create_experiment", description="Create a research experiment linked to a direction.")
def create_experiment(graph: Graph, title: str, hypothesis: str = "", direction_id: str | None = None) -> object:
    return create_experiment_fn(graph, title, hypothesis, direction_id)


TOOLS = [ingest_research_paper, create_idea_atom, create_experiment]
