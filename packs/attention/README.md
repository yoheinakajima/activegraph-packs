# Attention Pack

Neutral semantic observations for learning what deserves attention.

## What it owns

- `attention_observation` — a named signal about a subject: impression, open,
  foreground dwell, revisit, edit, reply, completion, dismissal, archive,
  explicit mark, eligible nonresponse, or an inspectable LLM judgment.
- `interaction_batch` — an idempotent bounded flush from one client session.
- The exposure rule: `nonresponse_window` fails closed without an
  `opportunity_id`; not shown and shown-but-ignored are different data.
- `importance_vector` — subject/objective/context/horizon-scoped salience,
  projected from semantic engagement and explicit outcomes with complete
  observation references.
- `source_trust_vector` — source/domain/query-scoped credibility, projected
  only from canonical helped/hurt/contradicted/stale outcomes.
- `rank_importance` — deterministic ranking with a named exploration reserve;
  unseen things are unranked, never silently low-value.

## Telemetry boundary

Clients send semantic observations only. Do not send mouse movements, scroll
ticks, keystrokes, DOM snapshots, CSS selectors, raw content, or background-tab
time. Active dwell is foreground-only and summarized in milliseconds.

## Vector boundary

The v1 policy is `importance-trust.beta-evidence@1`: conservative priors,
integer evidence weights, no ambient-time decay, and zero direct weight for an
LLM judgment. The score is a prediction, never a user reputation or source
self-description. Confidence remains claim-local; reliability remains
artifact-local; urgency and actionability remain domain semantics. BabyAGI may
render `priority_band`, but game score and authority may not consume it.
