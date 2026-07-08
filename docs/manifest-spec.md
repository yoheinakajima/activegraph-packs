# Pack Manifest Specification

**Status: FROZEN (2026-07-08), except §5. The freeze condition was
met: the runtime's Q1-Q8 answers are folded in (§9), and the evolution
pack consumes this spec in its static gates (manifest validity, both
hashes, and the two-way surface check run through
`activegraph.packs.manifest` inside passing acceptance fixtures).
Changes from here are spec amendments with changelog entries, agreed
with the runtime, never silent edits. §5 (sources, resolution, pins)
stays PROVISIONAL: its planned first consumer (the vc pack extraction)
was cancelled, so §5 holds the designed shape until a real multi-repo
consumer (a host pack-sources config) builds against it.**

## 1. Why a manifest

Today a pack is a Python module wired by convention: a `Pack(...)`
object, docstring notes like `requires=["core"]`, and an entry point in
pyproject.toml. That works for one repo of hand-written packs and fails
for everything now arriving:

- **Agent-authored packs** (evolution pack): a validator needs a
  machine-readable declaration to check code against, and an approval
  surface needs a stable identity (name, version, content hash) to pin
  what the owner reviewed.
- **Multi-repo loading** (third-party pack repos, a host's
  pack-sources config): an installer needs dependencies and
  compatibility ranges before it imports anything.
- **The runtime loader**: fail-loud validation at load time instead of
  import-time surprises.

The manifest is the static, declarative half of a pack. The `Pack(...)`
object remains the runtime artifact. The loader's job is to verify the
two agree.

Section 5 note: with the vc extraction cancelled, §5 currently has no
consuming implementation. It stays in the spec as the designed shape,
marked provisional, so a future pack-sources host starts from reviewed
rules instead of a blank page.

## 2. File format and location

One file, `manifest.toml`, at the pack root, next to `__init__.py`.

TOML because `tomllib` is stdlib on the Python versions this ecosystem
supports (3.11+), pyproject.toml set the precedent, and the format has
no code-execution surface. YAML would add a dependency; JSON has no
comments.

A pack without a manifest keeps loading exactly as today (grandfathering
is a loader policy decision, see open question Q2). A pack WITH a
manifest gets validated against it.

## 3. The schema

Illustrated with a realistic example, then field-by-field:

```toml
[pack]
name = "meeting_notes"
version = "0.1.0"
description = "Extracts decisions and action items from meeting transcripts."
license = "Apache-2.0"

[pack.provenance]
authors = ["Yohei Nakajima <yohei@example.com>"]
authored_by = "human"            # "human" | "agent"
generator = ""                   # agent identifier when authored_by = "agent"
source_url = "https://github.com/yoheinakajima/activegraph-packs"
created_at = "2026-07-08T00:00:00Z"

[pack.integrity]
content_hash = "sha256:9f8a..."  # see §4
# signature = ""                 # reserved, see §7

[dependencies]
activegraph = ">=1.3,<2.0"       # PEP 440 range
python = ">=3.11"
# PEP 508 strings; empty for most packs. The loader checks distribution
# presence (importlib.metadata), never installs (Q4). NOTE: this key must appear BEFORE the
# sub-tables below; in TOML, a bare key after a table header would belong
# to that sub-table.
python-deps = []

[dependencies.packs]
core = ">=0.1"
communication = ">=0.2"

[dependencies.optional-packs]    # integrates_with, checked at runtime
identity_auth = ">=0.1"
tool_gateway = ">=0.3"

[surface]
object_types = ["meeting", "decision", "action_item"]
relation_types = ["decided_in", "assigned_from"]
behaviors = ["transcript_ingester", "decision_extractor"]
tools = ["summarize_meeting"]
settings_schema = "MeetingNotesSettings"

# Gateway capability registrations this pack's HOST WIRING performs, with
# their risk classes (closed set: low | medium | high | critical). Empty
# for packs that register none. Verified statically, never by the loader;
# see the mechanism split below.
[[surface.capabilities]]
provider = "meeting"
capability = "export_summary"
risk_class = "medium"
credential_ref = ""              # name only, never a value

# Capabilities this pack's behaviors INVOKE at runtime (creates
# capability_call objects). Decision surfaces and reviewers read this to
# see a pack's outbound reach before approving it.
consumes = ["web.fetch_url"]

[fixtures]
entrypoint = "fixtures/run_fixtures.py"
deterministic = true             # asserts: no network, no API keys
```

### `[pack]` (required)

| Field | Rule |
|---|---|
| `name` | `^[a-z][a-z0-9_]{1,63}$`; unique within a load set; matches the directory name and the `Pack(name=...)` |
| `version` | PEP 440 (the ecosystem's version grammar everywhere else; a SemVer-looking `X.Y.Z` is valid PEP 440, so existing packs migrate unchanged); must match `Pack(version=...)` |
| `description` | nonempty; shown in catalogs and decision surfaces |
| `license` | SPDX identifier when nonempty; empty string allowed. Validators check SPDX validity only; the policy that published packs must carry a license lives in publisher tooling, where "published" is actually knowable |

### `[pack.provenance]` (required)

Provenance records where a pack came from. It never confers trust.
Trust is assigned by the installer (bundled, installed, agent-authored
are trust *tiers the host tracks*, never manifest claims), because a
manifest saying "trust me" is worth exactly nothing. `authored_by =
"agent"` is a disclosure flag consumers like the evolution pack's
decision surface render loudly; a missing or false flag is caught by
the host that generated the pack, not by the manifest itself.

### `[pack.integrity]` (required)

`content_hash` pins the bytes. See §4. `signature` is a reserved key so
the schema does not break when signing lands; validators MUST reject
ANY non-empty signature value today, because no algorithm is
implemented and "recognized prefix but unverifiable" must not become a
pass-through (Q6, sharpened by the runtime review).

### `[dependencies]` (required, sub-tables optional)

- `activegraph`: PEP 440 range, checked against the running runtime at
  load, fail-loud.
- `packs` (hard deps) and `optional-packs` (the existing
  `integrates_with` convention, checked lazily at runtime with graceful
  degradation, matching current pack behavior).
- `python-deps`: PEP 508. The loader verifies DISTRIBUTION PRESENCE via
  `importlib.metadata.version(dist_name)` and reports what is missing;
  it does not install, and it does not test importability (PEP 508
  names are distribution names, not import names: `pillow` installs,
  `PIL` imports).
  Agent-authored packs get an empty list enforced by the evolution
  pack's static gates (imports outside the allow-list already fail
  there).

### `[surface]` (required)

The declared capability surface. The two-way check ("everything
registered must be declared, everything declared must exist") is what
makes the manifest useful to the evolution pack's static gates and to
any reviewer: the declaration is the contract, the code cannot quietly
exceed it. Strictness level is Q3. It is enforced by TWO mechanisms,
because the surface has two kinds of members:

**Loader-verified at load time** (introspection of the `Pack(...)`
object): `object_types`, `relation_types`, `behaviors`, `tools`,
`settings_schema`. The identity mapping, so three implementations agree:

| Manifest list | Matches |
|---|---|
| `object_types` | each `ObjectType.name` in `Pack.object_types` |
| `relation_types` | each `RelationType.name` in `Pack.relation_types` |
| `behaviors` | each `Behavior.name` in `Pack.behaviors` |
| `tools` | each `Tool.name` in `Pack.tools` |
| `settings_schema` | `Pack.settings_schema.__name__` (empty string when the pack has no settings) |

**Statically verified, never by the loader**:
`[[surface.capabilities]]` and `consumes`. Gateway capability
registration is imperative host wiring (`register_local_capability`
calls at startup, sometimes conditional on optional packs), so the
loader cannot observe it at `load_pack` time. The check is an AST walk
of the pack source performed by CI in this repo and by the evolution
pack's static gates: every `register_local_capability` call site must
match a declared `[[surface.capabilities]]` entry and vice versa, and
every capability invocation must match `consumes`. Q8 asks the runtime
team whether a declarative `Pack.capabilities` field should exist so
this can move loader-side eventually.

`risk_class` is a closed set: `low | medium | high | critical`,
matching tool_gateway's `CapabilitySpec`. Validators reject anything
else.

`[[surface.capabilities]]` and `consumes` entries matter to governance
tooling: the capability catalog (task #7) and decision surfaces read
risk classes and outbound reach from here without importing the pack.

### `[fixtures]` (required)

Every pack ships runnable, deterministic fixtures. This has been the
repo's convention since v0.1.0; the manifest makes it checkable.
`deterministic = true` is an assertion the CI harness enforces (no
sockets, no key reads), not a wish.

Fixtures are test harness, never loaded runtime code, and are governed
by the `deterministic` assertion rather than by any import allow-list a
consumer applies to runtime source (the evolution pack's static gates
exempt the fixtures file for exactly this reason: a fixture entrypoint
legitimately needs `sys` and `pathlib`).

## 4. Content hash

`content_hash` = `"sha256:" + hex(sha256(canonical_bytes))` where
`canonical_bytes` is computed over the pack directory:

1. Collect every regular file under the pack root, recursively,
   EXCLUDING: `manifest.toml` itself (a hash cannot cover itself),
   `__pycache__/`, `*.pyc`, hidden files (`.` prefix), and symlinks
   (rejected outright, see Q5).
2. Sort the relative POSIX paths lexicographically (bytes).
3. For each file, append: the UTF-8 path, a NUL byte, the 8-byte
   big-endian file length, the raw file bytes.

Properties: platform-independent given identical bytes (no newline
normalization: the bytes are the contract; publish from one platform),
order-independent of filesystem enumeration, resistant to
file-boundary confusion (length prefix).

Edge rules, each normative (folded from the runtime's Q5 review):
directory symlinks are rejected exactly like file symlinks (a symlinked
subdir would smuggle unhashed content through a naive walk); paths that
do not encode as UTF-8 are rejected loudly; paths must be NFC-normalized
(macOS normalizes filenames and Linux does not, so an NFD `café.py`
would hash differently per platform); non-regular files (sockets, fifos)
are silently skipped like directories; empty directories are invisible
to the hash.

**Two hashes, two jobs.** The `content_hash` above excludes
`manifest.toml` because a hash cannot cover itself; it proves the
manifest matches the source files it ships with. That self-exclusion
makes it USELESS as an external pin: an attacker who swaps only the
manifest (risk classes relabeled, `consumes` emptied, `authored_by`
flipped, the exact document a reviewer reads) leaves `content_hash`
valid. External pins therefore use the **bundle hash**: the same walk
WITHOUT the manifest exclusion, covering every byte including
`manifest.toml`. `[load.pins]` values (§5) and the evolution pack's
proposal pins are bundle hashes. Reference implementations:
`activegraph.packs.manifest.compute_content_hash` and
`compute_bundle_hash` (runtime-owned; consumers import, never
reimplement).

Consumers: the runtime loader verifies `content_hash` at load (policy
per Q2); the evolution pack verifies the BUNDLE hash at gate time, at
adoption time, and at every boot re-materialization; multi-repo
installers verify the bundle hash after fetch. The exclusion list above
is normative for every consumer: any consumer phrase like "hash over
the pack's file set" means the file set as defined here.

## 5. Multi-repo loading (the shape, not the tool)

This spec defines what a pack source declaration points AT; the
resolver that fetches is host tooling (BabyAGI's pack-sources config,
this repo's CI). Sketch of the host-side source list the spec is
designed to support:

```toml
[[sources]]
name = "activegraph-packs"       # the general library
kind = "path"                    # "path" | "git"
location = "../activegraph-packs"
subdir = "packs"                 # pack root = <source>/<subdir>/<pack name>

[[sources]]
name = "activegraph-packs-research"
kind = "git"
location = "https://github.com/example/activegraph-packs-research"
ref = "9f2c41d..."               # commit SHA required unless pinned below
subdir = "packs"                 # default "packs"; "." for repo-root packs

[load]
packs = ["core", "tool_gateway", "chat", "research"]

[load.pins]                      # optional per-pack expected BUNDLE hashes (§4)
research = "sha256:ab12..."
```

Resolution rules, in order, all before anything imports:

1. **Pack root** is `<fetched source>/<subdir>/<pack name>` for every
   source kind, so §4's hash walk has a well-defined starting point.
2. **Precedence dominates**: a pack name resolves to the FIRST source
   that declares it. If that pick violates any pack's
   `[dependencies.packs]` range, the whole load fails loudly, and the
   error must name any satisfying versions found in lower-precedence
   sources so a human can reorder or repin. Precedence-then-fail is
   chosen over cross-source solving because it is deterministic,
   explainable in one sentence, and cannot silently pick a source the
   config author didn't intend.
3. **Dependency ranges** from every loaded pack's manifest must be
   simultaneously satisfiable, checked across the full load set. No
   partial loads.
4. **Hash verification, honestly scoped**: the post-fetch check against
   the manifest's own `content_hash` proves internal consistency
   (integrity in transit), never authenticity, because the manifest
   arrives with the pack and excludes itself from its own hash.
   Authenticity requires an EXTERNAL pin covering every byte INCLUDING
   the manifest: a `[load.pins]` entry, which is a **bundle hash**
   (§4), or a git `ref` that is a commit SHA (git already pins the
   manifest). A git source pinned by tag or branch without a
   `[load.pins]` bundle hash is a validation error, since tags move
   and the check would prove nothing.

## 6. Consumer pressure-test

| Consumer | What it needs | Where the spec answers |
|---|---|---|
| Runtime loader | machine-checkable schema, two-way surface check, structured errors | §3, Q1, Q3 |
| This repo's CI | validate 20 existing manifests, enforce fixtures promise | §3 fixtures, Q2 migration |
| Evolution pack | stable identity + hash pin, declared-vs-actual for static gates, risk classes visible pre-import, `authored_by` disclosure | §3 surface/integrity/provenance, §4 |
| future multi-repo host (§5, provisional) | dependency ranges resolvable cross-repo, source declarations | §3 dependencies, §5 |
| BabyAGI pack-sources | the §5 shape, trust tiers host-side, catalog metadata without import | §5, §3 provenance note, capabilities table |

The hardest consumer is the evolution pack, and it drove three
decisions: the hash canonicalization is exact enough to recompute
byte-identically in three places, the surface check is two-way, and
provenance is disclosure rather than trust.

## 7. Deliberately out of scope (seams left, nothing built)

- **Signing**: `signature` key reserved; algorithm choice, key
  distribution, and revocation are a separate design. The rule that
  future validators reject unknown algorithm prefixes is in the spec
  NOW so old validators cannot be downgrade-attacked later.
- **A registry/index protocol**: §5 sources are paths and git refs;
  a pack index is future host tooling.
- **Sandboxing claims**: the manifest declares, the loader verifies
  declarations; neither contains code execution.

## 8. Migration for the existing library

All 20 packs in this repo get manifests generated by a script (name,
version, description from `Pack(...)`; surface by import inspection;
hash computed; provenance filled human). CI then validates every pack's
manifest on every push, which keeps declared-vs-actual honest from day
one. Docstring conventions (`requires=`, `integrates_with=`) stay as
prose but stop being the source of truth.

## 9. Q1-Q8: answered (runtime review, folded 2026-07-08)

The reference implementation is `activegraph.packs.manifest` (v1.4).
This repo's CI and the evolution pack's gates IMPORT it; nothing here
reimplements validation or hashing.

- **Q1 (errors)): ANSWERED.** single `PackManifestError` carrying every
  violation at once, computed before anything else, matching
  `load_pack`'s pre-mutation atomicity.
- **Q2 (grandfathering)): ANSWERED, locked in CONTRACT v1.4 #1.** the
  runtime enforces nothing at `load_pack` this cycle; validation is
  opt-in via `activegraph.packs.manifest`. This repo's CI warns
  (validates every manifest on every push); loader warning arrives one
  minor after DRAFT exits; hard loader errors are 2.0 territory.
- **Q3 (strictness)): ANSWERED.** hard error when a manifest exists. A
  pack that opts in, opts all the way in; grandfathering is the soft
  path.
- **Q4 (python-deps)): ANSWERED, precision fix folded into §3.**
  check distribution presence via `importlib.metadata`, never
  importability, never install.
- **Q5 (hash edges)): ANSWERED, folded into §4.** directory symlinks
  rejected, non-UTF-8 paths rejected, NFC required, non-regular files
  silently skipped, empty directories invisible. The runtime also fixed
  its prompt loader (hidden files and symlinks skipped) so nothing the
  loader reads can escape the hash.
- **Q6 (signatures)): ANSWERED, sharpened.** ANY non-empty signature
  value is rejected today, not just unknown prefixes; no implemented
  algorithm means no pass-through.
- **Q7 (ownership)): ANSWERED.** the runtime owns schema and validation
  (`activegraph.packs.manifest`); this repo owns the spec document and
  the migration tooling until DRAFT exits.
- **Q8 (capability visibility)): ANSWERED; the mechanism chain, recorded.** (1) `Pack.capabilities: tuple[CapabilityDecl, ...]`
  shipped runtime-side in v1.4, giving the loader the same two-way
  check for capabilities as for behaviors/tools; (2) the loader records
  the declaration in the `pack.loaded` event payload, making it
  graph-readable; (3) gateway-side enforcement (tool_gateway checks at
  `register_local_capability` time that the registering pack declared
  the capability) is this repo's follow-up item once hosts pass pack
  identity at registration, and it retires the AST check's load-bearing
  role; the AST check stays as CI defense-in-depth against exactly the
  cases static analysis can see.
