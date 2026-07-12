# Connector Control Pack

The neutral control plane defined by ADRs 0033–0034. Service connectors keep
their authoritative provider-specific runs and explicitly adapt them into:

- a service/account/surface binding to one connector family;
- neutral run state, health, bounds, safe cursor presence, and maintenance;
- a run-scoped learning delta containing counts plus provenance refs;
- one validated native read shape: conversation, agenda, records, library, or
  telemetry.

This pack owns no OAuth, provider operation, payload parser, product copy,
ranking, or universal provider ontology. Route changes update a binding while
preserving service/account/surface identity.
