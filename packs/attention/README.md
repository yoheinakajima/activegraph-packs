# Attention Pack

Neutral semantic observations for learning what deserves attention.

## What it owns

- `attention_observation` — a named signal about a subject: impression, open,
  foreground dwell, revisit, edit, reply, completion, dismissal, archive,
  explicit mark, eligible nonresponse, or an inspectable LLM judgment.
- `interaction_batch` — an idempotent bounded flush from one client session.
- The exposure rule: `nonresponse_window` fails closed without an
  `opportunity_id`; not shown and shown-but-ignored are different data.

## Telemetry boundary

Clients send semantic observations only. Do not send mouse movements, scroll
ticks, keystrokes, DOM snapshots, CSS selectors, raw content, or background-tab
time. Active dwell is foreground-only and summarized in milliseconds.

## Deliberately deferred

This pack does **not** define an importance/trust scalar, vector, model, default
weights, ranking projection, priority policy, score, or authority input. ADR
0028 leaves the exact learned-vector representation to its implementation
round. This pack supplies the honest evidence floor for that decision.
