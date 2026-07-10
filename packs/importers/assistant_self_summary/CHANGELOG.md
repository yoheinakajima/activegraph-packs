# Changelog — assistant_self_summary

## 0.1.0 — 2026-07-10

- Paste-back (`manual`) and MCP-push (`mcp`) ingestion on one surface;
  identity by canonical content hash, so transports collapse to one
  evidence identity.
- Injection scan on ingestion; labels recorded in normalized metadata.
- Fail-loud bounds (empty, oversized) as `ingestion_failure` records.
- Inline replay mode (ephemeral pasted content).
