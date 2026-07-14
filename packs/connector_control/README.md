# Connector Control Pack

The neutral control plane defined by ADRs 0033–0034. Service connectors keep
their authoritative provider-specific runs and explicitly adapt them into:

- a service/account/surface binding to one connector family;
- neutral run state, health, bounds, safe cursor presence, and maintenance;
- a run-scoped learning delta containing counts plus provenance refs;
- one validated native read shape: conversation, agenda, records, library, or
  telemetry.

Conversation summaries include bounded latest-message preview/sender data,
interpretation state, and stable thread/message drill-down references. These
are family fields; provider-only fields remain in service extensions.

This pack owns no OAuth, provider operation, payload parser, product copy,
ranking, or universal provider ontology. Route changes update a binding while
preserving service/account/surface identity.

## Maintenance

`request_connector_refresh` creates a neutral `connector_maintenance_request`
and dispatches through a service-registered handler. The control plane checks
binding state, refresh availability, and already-running work; the service pack
alone interprets its cursor and proposes provider calls. Revoked bindings fail
closed. Run observations preserve last attempt/success and safe cursor coverage
for clients without exposing provider tokens.

## Ingestion plans

`connector_ingestion_plan` (ADR 0039) is the versioned, receipted acquisition
proposal: service-derived window derivation, per-surface expectations,
policy-sourced caps, and interpretation stages, with purpose
`initial_backfill`, `extension`, or `comprehension` (ADR 0045 consent plans).
Owner edits supersede; approval binds execution to the exact approved plan
version, and superseded plans can never execute. A service may register the
ADR 0041 three-phase seam (prepare/perform/commit) beside its synchronous
executor — begin/commit carry the same execution gates and fail closed on
mid-flight supersession, with `pending_deferred_plan_executions_fn` as the
host pump's poll.

## External work attempts

`external_work_attempt` (ADR 0041/0045) is the durable ledger under every
externally-performing work unit: crash-safe prepared/performing/
commit_pending/terminal phases keyed by an idempotency key, so a crash
between perform and commit can neither duplicate the external call nor lose
its result — restart commits the persisted outcome, retries under the
explicit attempt policy, or surfaces blocked/failed state. Payloads and
outcomes are bounded, secret-scanned JSON (oversized material blocks loudly);
`pending_commit_attempts_fn` is the restart worklist and
`project_external_work_attempts_fn` exposes every failure and retry, never a
silent one.

## Operational release policy

`connector_operational_policy` is the versioned ADR 0034 conformance floor,
pinning the 250-item fixture and its limits: acknowledgement/progress/read
latency, one cooperative execution quantum, events/annotations/behavior
firings per evidence, provider calls, replay bytes, and queue depth.
`connector-operational@0.2.0` named the acquisition ceilings ingestion plans
validate against (ADR 0039); `@0.3.0` raises `max_provider_calls` to 16 so an
ADR 0045 comprehension campaign stays inside one run's call budget.
`measure_connector_run` calculates a process-local report; wall-clock
observations are deliberately never written into the graph or used for
replayed decisions.
