# Changelog

## v0.1.0 — 2026-07-10 — Initial release

- Parse bounded official Claude data-export snapshots (ZIP or bare
  `conversations.json`, detected by extension and magic bytes).
- Emit one strict acquired-item/content handoff per chat message with
  content-addressed replay payloads.
- Preserve unknown content block types as explicit omission markers and fall
  back to the legacy plain `text` field.
- Record malformed exports and conversations without partial conversation
  evidence; valid siblings remain committed.
