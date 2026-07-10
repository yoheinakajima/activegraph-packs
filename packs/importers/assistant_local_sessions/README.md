# Assistant Local Sessions Importer

Parses local agent-session JSONL logs into Activity Normalizer acquisition
records. It is a format adapter only: it does not deduplicate, create
evidence, extract candidates, settle sources, or promote state.

## Supported layouts

One call imports one provider layout rooted at `root_path`. Default roots
live in `AssistantLocalSessionsSettings` (`~/.claude/projects`,
`~/.codex/sessions`); the caller expands `~` and passes an absolute path —
the importer never hardcodes home directories.

### Claude Code (`provider="claude_code"`)

```text
<projects root>/<encoded-project-path>/<session-id>.jsonl
```

One JSON object per line. Lines with `type` `user`/`assistant` carry a
`message` (`role`, `content` as a string or a part list), plus `uuid`,
`timestamp`, `sessionId`, and `cwd`. All other line types — `summary`,
`system`, `attachment`, `queue-operation`, title/mode records, hook output —
are skipped gracefully, as are user/assistant lines carrying only
`tool_use`/`tool_result`/`thinking` parts (no text).

### Codex (`provider="codex"`)

```text
<sessions root>/YYYY/MM/DD/rollout-<YYYY-MM-DDThh-mm-ss>-<uuid>.jsonl
```

Record shapes vary across Codex versions. The importer accepts the nested
shape `{"type":"response_item","payload":{"type":"message","role":...,
"content":[{"type":"input_text"|"output_text","text":...}]}}` and the older
flat `{"type":"message","role":...,"content":[...]}` variant. `session_meta`,
`event_msg`, `turn_context`, `world_state`, tool call records, and
non-user/assistant roles (`developer`, `system`) are skipped. The first
well-formed `session_meta` supplies the session id (and `cwd`); forked or
resumed rollouts append further `session_meta` records that do not change
identity. Fallback identity is the filename UUID, then the filename stem.

## Drift posture

Both formats are unversioned, append-only, and WILL drift between releases.
The parsers are defensive by design:

- A malformed line is skipped and counted (`malformed_lines`); it never fails
  the session.
- A non-message or text-free line is skipped and counted (`skipped_lines`).
- A malformed, oversized, or unreadable file records one recoverable
  `ingestion_failure` (`stage="acquisition"`) and sibling files continue.
- Content problems never raise out of the tool; only usage errors
  (bad provider, bad replay mode, missing surface, non-positive bounds) do.

## Bounded window

Every run imports at most `max_sessions` session files (default 20), the most
recent under a deterministic order key:

- Codex: the timestamp embedded in the rollout filename
  (`filename_timestamp`), with a relative-path tiebreak.
- Claude Code: file mtime with a relative-filename tiebreak
  (`file_mtime_then_name`). The observed mtime is recorded in message
  metadata; the importer never reads the wall clock.

An explicit `max_sessions` argument extends the window. The chosen window is
recorded in the returned `window` log (files considered, selected, imported,
failed, and skipped by the window), and a `backfill_cursor` per surface
advances over per-session stable refs.

## Message identity

One `acquired_item` + `acquired_content` pair per user/assistant message
line:

- `dedup_key` / `provider_item_id` = `"<session_id>:<message_uuid>"` when the
  line carries a uuid, else `"<session_id>:line-<line_number>"` (stable
  because the jsonl files are append-only).
- `source_hash` = SHA-256 of the raw line; `provider_time` from the line's
  own timestamp when it parses; `media_type` `application/json`.
- `source_category="ai_activity"`, `connection_path="local"`.

Re-import intentionally re-emits the same acquired identities; the Activity
Normalizer owns deduplication and revisioning, so an identical re-import
creates no new evidence revisions and an appended session file creates
evidence only for its new lines.

## Emitted graph objects

This pack owns no object or relation types. It emits the strict types owned
by `activity_normalizer`:

| Type | Purpose |
|---|---|
| `acquired_item` | Stable per-message identity, hashes, replay policy, importer version |
| `acquired_content` | Bounded message text plus session/line metadata |
| `backfill_cursor` | Stable oldest/newest per-session refs; never an offset |
| `ingestion_failure` | Explicit bounded file, line-bound, or artifact failure |

## Replay artifact layout

`artifact` is the default replay mode. The replay payload is the canonical
JSON of the normalized message unit, stored in the shared v0 CAS layout:

```text
<artifact_store_dir>/sha256/<first-two-hex>/<full-sha256-hex>
```

`inline` stores that same unit in the acquired record; `reference_only`
retains no payload and cannot support verified replay.

## Usage

```python
from activegraph import Graph
from packs.importers.assistant_local_sessions.tools import (
    import_assistant_local_sessions_fn,
)

graph = Graph()
result = import_assistant_local_sessions_fn(
    graph,
    "/Users/me/.claude/projects",
    "surface_claude_code_local",
    provider="claude_code",
    artifact_store_dir=".activegraph/replay-artifacts",
)
```

The registered `import_assistant_local_sessions` capability exposes the same
options. All parsing is keyless, deterministic, bounded, and offline.

## Fixtures

```bash
python packs/importers/assistant_local_sessions/fixtures/run_fixtures.py
```

Fixtures build fully synthetic session trees; no real session content is ever
copied into this repository.
