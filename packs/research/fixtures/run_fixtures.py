"""Research Pack fixtures — v0.1.

Fixture 1: paper_ingestion_pipeline
  A research_paper source is created. paper_ingester fires → Paper + Author + Venue.
  claim_extractor fires → observation objects.
  idea_atom_extractor fires → IdeaAtom objects.
  hypothesis_generator fires on high-coherence atoms → ResearchDirection.

Fixture 2: direction_synthesis
  Two papers with overlapping keywords are ingested.
  research_direction_synthesizer fires on the second atom batch.
  An experiment is created and linked to a direction.

Run:
    python packs/research/fixtures/run_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

from activegraph import Graph, Runtime
from packs.core import pack as core_pack, CoreSettings
from packs.research import pack as research_pack, ResearchSettings
from packs.research.behaviors import clear_research_registry
from packs.research.tools import (
    ingest_research_paper_fn,
    create_idea_atom_fn,
    create_experiment_fn,
)


def run_paper_ingestion_pipeline() -> bool:
    print("\n" + "=" * 60)
    print("Fixture: paper_ingestion_pipeline")
    print("  source → Paper + Author + Venue → claims → IdeaAtoms → Direction")
    print("=" * 60)

    clear_research_registry()

    graph = Graph()
    rt = Runtime(graph)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(research_pack, settings=ResearchSettings(
        min_coherence_for_hypothesis=0.5,
        max_ideas_per_paper=4,
        max_claims_per_paper=5,
        auto_synthesize_directions=True,
        min_ideas_for_direction=1,
    ))

    source = ingest_research_paper_fn(
        graph,
        title="Attention Is All You Need",
        abstract=(
            "We propose a new simple network architecture, the Transformer, based solely on "
            "attention mechanisms, dispensing with recurrence and convolutions entirely. "
            "Experiments on machine translation tasks show these models to be superior in "
            "quality while being more parallelizable and requiring significantly less time to train. "
            "We achieve 28.4 BLEU on the WMT 2014 English-to-German translation task, improving "
            "over the existing best results, including ensembles, by over 2 BLEU. "
            "On the WMT 2014 English-to-French translation task, we establish a new single-model "
            "state-of-the-art BLEU score of 41.0."
        ),
        authors="Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit",
        venue="NeurIPS 2017",
        year=2017,
        keywords=["attention", "transformer", "translation", "parallelization"],
    )
    rt.run_until_idle()

    papers = list(graph.objects(type="paper"))
    authors = list(graph.objects(type="author"))
    venues = list(graph.objects(type="venue"))
    observations = list(graph.objects(type="observation"))
    idea_atoms = list(graph.objects(type="idea_atom"))
    directions = list(graph.objects(type="research_direction"))

    print(f"\n  After paper ingestion:")
    print(f"  papers:     {len(papers)}")
    for p in papers:
        print(f"    [{p.id[:8]}] '{p.data.get('title')}' year={p.data.get('year')}")
    print(f"  authors:    {len(authors)}")
    for a in authors[:4]:
        print(f"    {a.data.get('name')}")
    print(f"  venues:     {len(venues)}")
    for v in venues:
        print(f"    '{v.data.get('name')}' kind={v.data.get('kind')}")
    print(f"  observations (claims): {len(observations)}")
    print(f"  idea_atoms: {len(idea_atoms)}")
    for atom in idea_atoms[:3]:
        print(f"    [{atom.id[:8]}] coherence={atom.data.get('coherence_score'):.2f} '{atom.data.get('text')[:60]}'")
    print(f"  research_directions: {len(directions)}")
    for d in directions[:2]:
        print(f"    [{d.id[:8]}] status={d.data.get('status')} '{d.data.get('title')[:50]}'")

    # Check relations
    all_rels = list(graph.relations())
    rel_types = {r.type for r in all_rels}
    print(f"\n  relation types seen: {sorted(rel_types)}")

    failures = []
    if not papers:
        failures.append("paper_ingester created no Paper objects")
    if not authors:
        failures.append("paper_ingester created no Author objects")
    if not venues:
        failures.append("paper_ingester created no Venue objects")
    if not idea_atoms:
        failures.append("idea_atom_extractor created no IdeaAtom objects")
    if not directions:
        failures.append("hypothesis_generator created no ResearchDirection objects")
    if "authored_by" not in rel_types:
        failures.append("Missing relation: authored_by")
    if "published_in" not in rel_types:
        failures.append("Missing relation: published_in")
    if "proposes_idea" not in rel_types:
        failures.append("Missing relation: proposes_idea")

    if failures:
        print("  FAIL:")
        for f in failures:
            print(f"    {f}")
        return False

    print("  PASS")
    return True


def run_direction_synthesis() -> bool:
    print("\n" + "=" * 60)
    print("Fixture: direction_synthesis")
    print("  Two papers → overlapping atoms → synthesized direction + experiment")
    print("=" * 60)

    clear_research_registry()

    graph = Graph()
    rt = Runtime(graph)
    rt.load_pack(core_pack, settings=CoreSettings())
    rt.load_pack(research_pack, settings=ResearchSettings(
        min_coherence_for_hypothesis=0.45,
        max_ideas_per_paper=3,
        auto_synthesize_directions=True,
        min_ideas_for_direction=2,
    ))

    # Paper 1: BERT
    ingest_research_paper_fn(
        graph,
        title="BERT: Pre-training of Deep Bidirectional Transformers",
        abstract=(
            "We introduce BERT, which stands for Bidirectional Encoder Representations from Transformers. "
            "BERT is designed to pre-train deep bidirectional representations from unlabeled text "
            "by jointly conditioning on both left and right context in all layers. "
            "The pre-trained BERT model can be fine-tuned with just one additional output layer "
            "to create state-of-the-art models for a wide range of tasks."
        ),
        authors="Jacob Devlin, Ming-Wei Chang, Kenton Lee, Kristina Toutanova",
        venue="NAACL 2019",
        year=2019,
        keywords=["bert", "pretraining", "transformers", "bidirectional", "representations"],
    )
    rt.run_until_idle()

    # Paper 2: GPT
    ingest_research_paper_fn(
        graph,
        title="Language Models are Unsupervised Multitask Learners",
        abstract=(
            "Natural language processing tasks, such as question answering, machine translation, "
            "reading comprehension, and summarization, are typically approached with supervised learning. "
            "We demonstrate that language models begin to learn these tasks without any explicit supervision "
            "when trained on a new dataset of millions of webpages called WebText. "
            "The capacity of the language model is essential to the success of zero-shot task transfer."
        ),
        authors="Alec Radford, Jeffrey Wu, Rewon Child, David Luan",
        venue="OpenAI Blog",
        year=2019,
        keywords=["language", "transformers", "pretraining", "zero-shot", "multitask"],
    )
    rt.run_until_idle()

    papers = list(graph.objects(type="paper"))
    idea_atoms = list(graph.objects(type="idea_atom"))
    directions = list(graph.objects(type="research_direction"))

    print(f"\n  After two papers:")
    print(f"  papers:              {len(papers)}")
    print(f"  idea_atoms:          {len(idea_atoms)}")
    print(f"  research_directions: {len(directions)}")
    for d in directions[:3]:
        atom_ids = d.data.get("idea_atom_ids") or []
        print(f"    [{d.id[:8]}] confidence={d.data.get('confidence'):.2f} "
              f"atoms={len(atom_ids)} '{d.data.get('title')[:50]}'")

    # Create experiment linked to first direction
    if directions:
        direction_id = directions[0].id
        exp = create_experiment_fn(
            graph,
            title="Comparing BERT vs GPT pretraining on low-resource tasks",
            hypothesis="Bidirectional context (BERT) outperforms unidirectional (GPT) on NLU benchmarks",
            direction_id=direction_id,
        )
        rt.run_until_idle()

        experiments = list(graph.objects(type="experiment"))
        print(f"\n  After creating experiment:")
        print(f"  experiments: {len(experiments)}")
        for e in experiments:
            print(f"    [{e.id[:8]}] status={e.data.get('status')} '{e.data.get('title')[:50]}'")

    # Check relations
    all_rels = list(graph.relations())
    rel_types = {r.type for r in all_rels}
    print(f"\n  relation types seen: {sorted(rel_types)}")

    failures = []
    if len(papers) < 2:
        failures.append(f"Expected 2 papers, got {len(papers)}")
    if not idea_atoms:
        failures.append("No IdeaAtoms created from either paper")
    if not directions:
        failures.append("No ResearchDirections synthesized")
    if "composes_direction" not in rel_types:
        failures.append("Missing relation: composes_direction")
    if directions and "tests_direction" not in rel_types:
        failures.append("Missing relation: tests_direction (experiment → direction)")

    if failures:
        print("  FAIL:")
        for f in failures:
            print(f"    {f}")
        return False

    print("  PASS")
    return True


def run_all() -> None:
    results = [
        run_paper_ingestion_pipeline(),
        run_direction_synthesis(),
    ]
    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"Research Pack: {passed}/{total} fixtures passed")
    print("=" * 60 + "\n")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    run_all()
