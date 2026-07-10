# Importers

Importers are small, independently loadable format adapters. Each one parses
one source format and emits the graph-visible handoff owned by
`activity_normalizer`:

```text
provider / file / export
  -> importer
      -> acquired_item
      -> acquired_content
  -> activity_normalizer
      -> evidence revisions and typed candidates
```

The boundary is strict: importers do not calculate evidence identities, dedup
deliveries, settle sources, extract candidates, or promote state. Repeated and
overlapping snapshots deliberately emit the same provider identity again; the
normalizer reconciles them.

## Acquired-item handoff

Every valid source item first creates an `acquired_item` with exactly these
fields:

| Field | Meaning |
|---|---|
| `source_surface_id` | Stable logical connection-surface id supplied by the caller |
| `provider_item_id` | Stable provider id when available |
| `dedup_key` | Stable provider id or canonical content identity |
| `source_ref` | Provider id, path, archive member, or URL |
| `source_hash` | Lowercase SHA-256 hex of the provider item, when available |
| `provider_time` | Provider timestamp carried as evidence, or `null` |
| `replay_mode` | `inline`, `artifact`, or `reference_only` |
| `replay_payload_ref` | Inline value, artifact URI, or explicit non-payload sentinel |
| `replay_payload_hash` | Lowercase SHA-256 hex of the exact normalizer input |
| `media_type` | Explicit media type |
| `importer_id` / `importer_version` | Versioned parser identity |

It then creates `acquired_content`, referring to the acquired object's graph
id. This second object carries bounded provider-neutral reasoning content,
normalization metadata, canonical source category, connection path, and the
fixture flag. Activity Normalizer reacts to the acquired item after the paired
handoff is committed, so it never has to guess a format or re-fetch the source.

## Replay artifacts

`artifact` is the normal default. Exact normalizer-input bytes are stored
outside the graph log in a local content-addressed store:

```text
<artifact_store>/sha256/<first-two-hex>/<full-sha256-hex>
```

The graph reference is
`artifact://sha256/<first-two-hex>/<full-sha256-hex>`. Writes are atomic and an
existing artifact is hash-verified before reuse.

`inline` puts the exact payload in the acquisition record and is intended for
small or ephemeral content. `reference_only` records
`reference_only:no-payload`, retains only bounded derived content, reports
replay-incomplete downstream, and must never cause a replay to contact the
source again.

## Included importers

- `local_files` — bounded, sorted snapshots of UTF-8 `.txt`, `.md`,
  `.markdown`, and `.json` files; category `local_knowledge`, path `local`.
- `chatgpt_export` — official ChatGPT export ZIP adapter; category
  `ai_activity`, path `export`.

## Adding an importer

1. Copy `packs/_template` to `packs/importers/<source>` and keep the standard
   files, fixture runner, README, changelog, and manifest.
2. Use a leaf pack name matching `<source>` and declare
   `# requires=["activity_normalizer"]` in `__init__.py`.
3. Parse and validate the complete provider item before emitting it. Required
   identity fields fail loudly; malformed input records `ingestion_failure`
   and creates no partial acquired pair for that item.
4. Commit one acquired pair at a time and advance `backfill_cursor` after each
   pair using provider-stable references only. Never store page numbers or
   offsets.
5. Put only the minimum exact normalizer input in replay storage. Exclude
   unused provider envelopes and unprocessed attachments.
6. Test duplicate delivery, snapshot overlap, interruption-safe progress,
   edits, revocation/artifact loss posture, bounded input, and malformed data.
   Fixtures must be keyless, offline, and deterministic.
7. Register the nested pack entry point, add it to manifest/fixture inventory,
   regenerate its manifest, and add its explicit CI fixture step.

New gateway-exposed surfaces are capabilities. A learned reusable artifact is
a skill; do not use that word for importer tools.
