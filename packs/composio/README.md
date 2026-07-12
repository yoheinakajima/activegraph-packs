# Composio route pack

Thin Tier-B route support. It creates hosted Connect Links for one selected
service and checks that service's connection status. It does not enumerate or
ingest the Composio catalog. Enabled connectors may resolve a bounded list of
their own provider-tool candidates so `latest` hardens into a concrete version
and schema fingerprint before execution. Enabled services (Gmail first) own canonical
capability ids and integration profiles; `composio` remains route metadata.

The SDK is optional: install `activegraph-packs[composio]` and register
`COMPOSIO_API_KEY` through the Secrets pack resolution seam. Environment wins;
an embedding host may supply a managed/process-memory credential source.
Connect Links stay process-ephemeral and are never written
to graph events; the graph records their request id, origin, and a
sanitizer-safe chunked SHA-256 fingerprint only.
