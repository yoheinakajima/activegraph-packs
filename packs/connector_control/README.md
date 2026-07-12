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

## Operational release policy

`connector_operational_policy` is the versioned ADR 0034 conformance floor.
Version `connector-operational@0.1.0` pins the 250-item fixture and its limits:
acknowledgement/progress/read latency, one cooperative execution quantum,
events/annotations/behavior firings per evidence, provider calls, replay bytes,
and queue depth. `measure_connector_run` calculates a process-local report;
wall-clock observations are deliberately never written into the graph or used
for replayed decisions.
