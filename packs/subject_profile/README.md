# Subject Profile

Reusable knowledge about a person or subject. This is deliberately separate
from `agent_profile`, which describes the assistant. An annotation may propose
a `profile_candidate`; only an explicit verdict can promote a `subject_fact`.
Every fact retains candidate, annotation, evidence, surface, confidence, and
trust references. Corrections create contradictions; forgetting creates a
superseding tombstone rather than deleting history.

Promotion is idempotent by value, and contradictions apply only to declared
single-valued attributes — every other attribute accumulates: a second
confirmed handle, url, project, or company is more identity, not a conflict
(ADR 0042). An owner re-declaration supersedes the owner's own prior
self-declared fact for the same attribute/platform scope (carried in verdict
metadata as `declaration_scope`, e.g. `self:handle:github`): editing "my
github handle" replaces the old handle instead of accumulating beside it.
Facts without a scope — independently sourced facts above all — are never
superseded by a declaration, and a superseded prior is a correction, never a
contradiction.

