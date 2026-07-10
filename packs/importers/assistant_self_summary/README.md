# Assistant Self-Summary Importer

One surface (`assistant_self_summary`) for the assistant-as-source seed
(ADR 0025): an owner pastes their assistant's self-summary (`manual`
transport) or a connected assistant pushes the same text over MCP
(`mcp` transport).

## Identity rule

The dedup key is the SHA-256 of the canonical summary text (newlines
normalized, outer whitespace stripped). **The same summary through
either transport produces the same evidence identity** — transport is
connection-path metadata, never identity. Re-submission is a no-op at
the evidence layer.

## Untrusted by construction

Summary text is untrusted external content (ADR 0023): it is
injection-scanned on ingestion (`scan_for_injection` labels land in the
normalized metadata for audit), and the pipeline shape keeps hostile
text inert — evidence → annotations → candidates only. Nothing an
imported summary says can act, approve, or escalate.

## Lossy seed

A self-summary complements exports (`claude_export`,
`chatgpt_export`) and `assistant_local_sessions`; it never replaces
them. Replay mode is `inline` (pasted text is ephemeral unique content,
ADR 0013/0015).
