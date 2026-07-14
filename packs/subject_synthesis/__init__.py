"""Subject synthesis: determinism floors, synthesis proposes, verdicts promote.

ADR 0043 / D064. A bounded, provider-gated comprehension pass reads what is
already confirmed or owner-scoped — promoted subject facts (with their
class: identity, narrative, instruction), the owner's connector taxonomy,
and recurring entities — and proposes STRUCTURED candidates: identity
attributes lifted from prose, and a curated project slate with reasoned
rationale. Every proposal must cite the evidence refs it reasons from or
it is dropped at commit; all outputs enter the existing candidate →
owner-verdict → promotion pipelines. Synthesis can never mint a fact, a
project, or a memory. Zero-key stores keep the deterministic floor.

The execution shape mirrors semantic_extraction's deferred seam (ADR
0041): a durable ``subject_synthesis_request`` is the work unit; hosts
run prepare (graph reads) → perform (provider only) → commit (graph
writes) on their pump, or the synchronous composition inline.
"""

from activegraph.packs import Pack

from .engine import (
    commit_subject_synthesis_fn,
    pending_subject_synthesis_requests_fn,
    perform_subject_synthesis,
    prepare_subject_synthesis_fn,
    request_subject_synthesis_fn,
    run_subject_synthesis_fn,
)
from .object_types import OBJECT_TYPES
from .settings import SubjectSynthesisSettings

# requires=["activity_normalizer", "subject_profile", "projects"],
# integrates_with=["entity", "connector_control", "llm_provider"]
pack = Pack(
    name="subject_synthesis",
    version="0.2.0",
    description=(
        "Bounded, provider-gated comprehension synthesis: structured "
        "identity candidates lifted from confirmed prose and a curated, "
        "evidence-cited project slate — verdict-gated, receipted, with "
        "the deterministic derivation left untouched as the zero-key floor."
    ),
    object_types=tuple(OBJECT_TYPES),
    relation_types=(),
    behaviors=(),
    tools=(),
    policies=(),
    prompts=(),
    settings_schema=SubjectSynthesisSettings,
)

__all__ = [
    "pack",
    "SubjectSynthesisSettings",
    "commit_subject_synthesis_fn",
    "pending_subject_synthesis_requests_fn",
    "perform_subject_synthesis",
    "prepare_subject_synthesis_fn",
    "request_subject_synthesis_fn",
    "run_subject_synthesis_fn",
]
