# Changelog

## v0.1.0 — 2026-07-10 — Initial release

- Parse local Claude Code (`projects/<encoded-path>/<session-id>.jsonl`) and
  Codex (`sessions/YYYY/MM/DD/rollout-*.jsonl`) session logs defensively.
- Bounded most-recent-files window (default 20) with a deterministic order
  key, a recorded window log, and per-session backfill cursor advancement.
- Emit strict acquired-item/content handoffs per user/assistant message line
  with content-addressed replay payloads.
- Skip and count malformed or non-message lines; record recoverable
  ingestion failures for unreadable or oversized files without failing runs.
