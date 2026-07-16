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

ADR 0047 adds the governed agentic layer: understanding affordances by
which any source joins a campaign, source lenses contributing to one
versioned working understanding with support-vs-context lineage, a
dynamic coordinator whose moves the deterministic host validates, and
bounded reasoning-model evidence drill-downs. The coordinator never
holds credentials, mutates canonical truth, or grants itself authority.
"""

from activegraph.packs import Pack

from .affordance import (
    affordance_catalog_fn,
    get_understanding_affordance,
    register_understanding_affordance,
    registered_understanding_affordances,
    unregister_understanding_affordance,
    validate_understanding_affordance,
)
from .coordinator import (
    answer_owner_question_fn,
    ask_owner_question_fn,
    current_campaign_fn,
    open_comprehension_campaign_fn,
    project_comprehension_campaign_fn,
    propose_next_move_deterministic_fn,
    record_coordinator_move_fn,
    validate_coordinator_move_fn,
)
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
from .working import (
    compose_working_understanding_fn,
    contribute_source_lens_fn,
    ensure_source_lens_fn,
    project_working_understanding_fn,
    settle_source_lens_fn,
)

# requires=["activity_normalizer", "subject_profile", "projects"],
# integrates_with=["entity", "connector_control", "llm_provider"]
pack = Pack(
    name="subject_synthesis",
    version="0.4.0",
    description=(
        "Bounded, provider-gated comprehension synthesis: structured "
        "identity candidates lifted from confirmed prose and a curated, "
        "evidence-cited project slate — verdict-gated, receipted, with "
        "the deterministic derivation left untouched as the zero-key floor. "
        "ADR 0047 adds understanding affordances, source lenses over one "
        "versioned working understanding with support-vs-context lineage, "
        "and a governed dynamic coordinator with bounded drill-downs."
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
    "affordance_catalog_fn",
    "answer_owner_question_fn",
    "ask_owner_question_fn",
    "commit_subject_synthesis_fn",
    "compose_working_understanding_fn",
    "contribute_source_lens_fn",
    "current_campaign_fn",
    "ensure_source_lens_fn",
    "get_understanding_affordance",
    "open_comprehension_campaign_fn",
    "pending_subject_synthesis_requests_fn",
    "perform_subject_synthesis",
    "prepare_subject_synthesis_fn",
    "project_comprehension_campaign_fn",
    "project_working_understanding_fn",
    "propose_next_move_deterministic_fn",
    "record_coordinator_move_fn",
    "register_understanding_affordance",
    "registered_understanding_affordances",
    "request_subject_synthesis_fn",
    "run_subject_synthesis_fn",
    "settle_source_lens_fn",
    "unregister_understanding_affordance",
    "validate_coordinator_move_fn",
    "validate_understanding_affordance",
]
