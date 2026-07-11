# Composio route pack

Thin Tier-B route support. It creates hosted Connect Links for one selected
service and checks that service's connection status. It does not enumerate or
ingest the Composio catalog. Enabled services (Gmail first) own canonical
capability ids and integration profiles; `composio` remains route metadata.

The SDK is optional: install `activegraph-packs[composio]` and set
`COMPOSIO_API_KEY`. Connect Links stay process-ephemeral and are never written
to graph events; the graph records their request id, origin, and a
sanitizer-safe chunked SHA-256 fingerprint only.
