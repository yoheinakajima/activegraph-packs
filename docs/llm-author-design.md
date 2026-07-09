# LLM Author: design

**Status: BUILT, MOCK-MODEL ONLY, LIVE OPERATION GATED ON THE SOAK. The
author is implemented in `packs/evolution/author.py` and proven against
a MOCK model with deterministic, keyless fixtures. Gates 1 (subprocess
isolation), 2 (independent design review), 3 (drafting records render on
the decision surface), and 6 (rate/budget caps) are MET; gate 4's
enforced-boundary fixtures run against the real author path. Gate 5 (a
green soak) is pending the soak's green finish, now running on 1.7.1 on
both platforms. HARD RULE: the author is never pointed at a live model
on a credentialed machine until gate 5 clears. Keyless mock operation is
what makes building and proving it safe before the soak finishes.**

## History

- **2026-07-08, author BUILT (mock model only).** `author.py` implements
  §3 origin-based frame assembly (four fixed sections, every excluded
  origin provably absent), the sealed drafting record with taint
  recomputed from admitted ids, the one-shot no-tools model call, pack-
  owned name/provenance (`agent_` prefix, `authored_by` stamped by pack
  code, the model produces four source bodies and nothing else), and the
  §5 rate/budget caps. Evolution fixtures 25-28 cover origin assembly and
  exclusions, the pipeline and the four folds under the real author, the
  taint-plus-caps behavior, and the gate-3 end-to-end render. Live-model
  operation stays gated on gate 5 (the soak).
- **2026-07-08, design review complete (build gate 2).** Approved: the
  origin-over-content posture is the correct and only defensible
  architecture; the no-tools-during-drafting property is airtight; and
  building the drafting-record renderer ahead of the author was the
  right sequencing. Four changes required and folded, each turning an
  ASSERTED trust boundary into an ENFORCED one with a fixture: charter
  integrity as a gate (§3a/§8), drafting-record tamper-evidence via
  taint recompute (§4), the exception-message question closed as NO for
  v1 (§3b/§8), and structured-field charset validation (§3b/§6). The
  reviewer flagged the exclusion of prior proposals' rationales (§3/§5)
  as well-caught and kept as-is.

The evolution pack (docs/evolution-design.md) runs gap, proposal,
gates, trial, held approval, adoption. Its `submit_proposal_fn` is the
one authoring entry point, and everything downstream treats a scripted
generator, an owner-drafted pack, and a future LLM author identically.
This document designs that future author: the component that reads a
`capability_gap` and drafts candidate pack source with a model.

## 1. The threat this design is against

An LLM author is where prompt injection meets code generation. The
output side is already governed: gates that parse rather than trust,
a fork trial, a bundle-hash pin, and a verified owner reading the full
diff on the decision surface. The unguarded side is the INPUT: the
drafting context. A model that reads attacker text while holding a
code-emitting pen can be steered into writing the attacker's pack, and
the pack will be syntactically clean, gate-compliant, and plausible on
review.

The existing taint machinery covers one path into that context. Gap
lineage is deterministic: `open_reflection_gap_fn` unions
`injection_flags` from every reviewed `capability_result`, the
gatekeeper suspends tainted proposals before gating, and no model
choice can launder the flags away. What gap lineage does NOT cover:

- A poisoned memory. The memory packs store text distilled from
  conversations, and conversations quote tool output, web content, and
  channel messages from arbitrary senders. A memory can carry an
  instruction planted weeks before the gap existed, with no
  injection_flags anywhere on the gap's lineage.
- A poisoned profile. `agent_profile` goals and instructions are
  patchable state; anything that ever got write access can leave
  standing orders the author would read as owner intent.
- Poisoned failure evidence. The gap's underlying
  `capability_result.output_data` is tool-derived text by definition.
  Its flags inherit, but the tripwire is regex heuristics, and an
  unflagged result is unflagged text, never clean text.

That is the hole this design closes: the author frame must be built
so that text of those origins either never enters it, or enters
neutralized and leaves a deterministic mark on the proposal.

## 2. Posture in one line

Classify by ORIGIN, never by content. Content scanning stays what it
has always been here, a tripwire that flags and audits. Admission to
the author frame is decided by where text came from, and the default
for every origin is OUT.

## 3. The drafting context, assembled

The author frame is built by pack code (deterministic assembly), never
by the model choosing what to read. It has exactly four sections, in a
fixed order, and nothing else.

**(a) The charter.** The system prompt: what a pack is, the authored
file set, the import allow-list, the size caps, the manifest format,
the house constraints (registration signatures, settings defaults).
Repo-shipped, version-controlled, and hash-pinned: the charter's
sha256 is recorded on the drafting record, so "which instructions was
the author under" is answerable forever. No runtime string
interpolation into the charter, ever.

The charter is the ONE fully-trusted origin in the frame, so its trust
is enforced, not merely asserted. Three rules, all mechanical:

- **Human-PR-only.** The charter changes through the same PR review as
  any repo file, by a human. No runtime path writes it.
- **Never an authorable target.** The charter path is a reserved
  authored path, in the same gate family as the reserved capability
  namespaces. A proposal whose file set includes the charter path is
  refused at `static:reserved_paths`, the FIRST gate, before file-set
  or manifest checks run (implemented: `analysis.check_reserved_paths`,
  `EvolutionSettings.reserved_paths`, default `author_charter.md`).
- **Charter improvement is permanently out of scope for any autonomous
  loop**, not just v1. A system that tunes its own author's
  instructions has removed the one origin the whole posture trusts.
  This is a standing rule, not a deferral.

**(b) The gap, structured.** From the `capability_gap` and its
evidence chain, STRUCTURED FIELDS ONLY: capability name, provider,
risk class, exception_type, failure counts, the object types involved.
The free-text fields (`description`, `capability_result.output_data`,
message bodies, tracebacks beyond the exception type and frame names)
do not cross. A traceback quotes the failing input; the failing input
is the attacker's text in the tool-failure case. If the owner wants
the author to see prose about the gap, the owner writes that prose
(section d).

The exception MESSAGE never crosses either, closed as NO for v1: the
message embeds attacker input by construction (a failing capability
call quotes what it was called with), and there is no honest
"sanitized" for text whose whole risk is that it is prose. Admit the
exception TYPE and frame names only. Debuggability is served by the
owner reading the actual `capability_result` on the GOVERNED decision
surface, never by piping it into the UNGOVERNED author frame. A
"lightly sanitized message" would split the difference and gut the
origin rule, so the door stays shut.

"Structured fields only" is enforced by charset, not by intent: a
capability NAME that reads like a sentence is prose wearing an
identifier's slot. At frame assembly every admitted field is validated
against the manifest charset the runtime already defines
(`^[a-z][a-z0-9_]{1,63}$` for provider/capability names, a dotted
identifier for exception_type, the `low|medium|high|critical` set for
risk class, an int for counts), and a field path outside this closed
allow-list is inadmissible entirely. A field carrying prose-shaped text
is REFUSED, not passed through as an "identifier"
(implemented: `author_frame.validate_structured_fields`, enforced as a
submission backstop in `submit_proposal_fn`). This bounds the residual
channel §6 names to actual identifiers.

**(c) The target surface, from source of truth.** Manifests of the
packs the candidate will sit beside (declared surfaces, consumed
capabilities), the object-type schemas it must write against, and the
relevant runtime contract excerpts. All repo-shipped or
loader-introspected. Nothing here originated from a conversation.

**(d) Owner text, verified.** Free-text drafting guidance is admitted
from exactly one origin: `chat_input` whose sender resolved to a
verified principal holding an approver role (the identity_auth check
the adoption capabilities already require). It crosses wrapped in the
tool_gateway EXTERNAL CONTENT envelope anyway, and it still passes
`scan_for_injection`; a hit flags the drafting record rather than
blocking, same tripwire semantics as everywhere else.

Explicitly and permanently excluded from the frame, v1: memory pack
retrieval results, agent_profile goals and instructions, any
`capability_result.output_data`, any web or MCP tool text, any channel
message from an unverified sender, and prior proposals' rationales
(a rejected proposal's rationale is model text that may itself be the
payload). Exclusion is the mechanism that needs no correctness proof;
neutralization is reserved for the one origin (owner text) where
exclusion would gut the feature.

## 4. The drafting record

Every draft writes a `drafting_context` object BEFORE the model call:
the charter hash, the gap id, the exact object ids and field paths
admitted under (b) and (c), the owner chat_input ids admitted under
(d), and the injection flags found on any of them. The record is SEALED
before the model call and immutable after: the model's output is a file
set, and file sets cannot patch the record.

The taint enforcement does not trust the record's stored flags field.
`submit_proposal_fn` RECOMPUTES the injection-flag union from the
objects the record ADMITTED, read fresh from the graph by id, and the
proposal inherits that recomputed union, never the value the record
stores. This is the §4 analog of gap-lineage's "no model choice can
launder the flags" rule, applied where it matters most: a record that
lies about its own cleanliness (or a record-corruption bug that empties
the flags field) while admitting a tainted object still yields a
suspended proposal, because the union is derived from the admitted ids,
not from any field a writer could tamper with (implemented:
`author_frame.recompute_drafting_taint`; fixture: a record with an
empty stored flags field but a flagged admitted owner input still
suspends). A tainted context yields a suspended proposal even when the
model output looks pristine, and the decision surface renders the
drafting record next to the diff so the owner sees what the author
read, never just what it wrote.

## 5. The author frame's mechanics

- **One shot, no tools.** The author gets the assembled context and
  returns source. It holds zero gateway capabilities during drafting:
  no search, no memory recall, no fetch. As BUILT, the frame handed to
  the model is a pure-data dict with no graph handle and no capability,
  so "no tool surface at all" holds by construction: there is nothing
  to call. Fixture 26 serializes the frame to JSON to prove it. If
  drafting needs more context, that is a gap in (b) or (c) assembly,
  fixed in pack code where it is reviewable.
- **Output is source bodies, nothing else.** As BUILT
  (`author.py::AuthoredSource`), the model returns exactly four source
  bodies (object types, behaviors, settings, tools) and nothing else.
  Pack code owns `__init__.py`, the fixtures, the pinned trial driver,
  and the manifest with its provenance, so the model STRUCTURALLY
  cannot set the pack name (an `agent_` prefix it never sees), cannot
  set provenance, and cannot emit the charter path. The proposal is
  submitted with `authored_by="llm"` (the decision-surface banner reads
  this); the manifest's coarse runtime flag is `agent` (the runtime's
  provenance vocabulary is human/agent). Content hash is the runtime's.
- **The decision surface shouts.** `authored_by="llm"` renders the
  loud author banner, plus the drafting record: charter hash, admitted
  ids, flags. An owner approving an LLM-authored pack sees what it
  read and what it wrote, on one page (fixture 28, gate 3).
- **Budget and rate.** As BUILT: at most one draft in flight per gap, a
  daily cap on drafts (`EvolutionSettings.max_drafts_per_day`), and no
  redrafting loops. Assembly never reads a prior proposal or its gate
  details, so a retry is a fresh draft from the same (a) through (d)
  context; there is no channel to feed a rejection back. Fixture 27
  covers all three caps.

## 6. What this does NOT claim

- It does not claim the model cannot write a malicious pack from a
  clean context. The gates, the trial, the pins, and a reading owner
  remain the containment for that, and the size caps exist so the
  reading is real.
- It does not claim the regex tripwire catches injections. Origin
  classification is the control; the tripwire is an audit trail.
- It does not claim structured fields are unpoisonable. A capability
  NAME could in principle carry a payload, so §3b charset-validates
  every admitted field against the manifest identifier pattern and
  refuses prose-shaped values: the channel is bounded to identifiers,
  which is as small as this gets. A payload that fits inside a valid
  `^[a-z][a-z0-9_]{1,63}$` identifier is the residue that remains, and
  the gates, trial, pins, and reading owner are its containment.
- It does not cover a hostile owner. A verified approver steering the
  author toward a bad pack is out of scope by definition: that person
  already holds approval power.

## 7. Gate conditions to build (all of them, no substitutions)

1. **MET.** Runtime subprocess isolation for fork trials, shipped
   (v1.5) and consumed here, so a hostile candidate's first execution
   happens outside the parent process. On 1.7.x the child imports
   activegraph from the parent's resolved path with the env allow-list
   closed.
2. **MET** (see History, 2026-07-08): design reviewed by someone who
   did not write it, with the review recorded here; approved with four
   required changes, all folded and enforced with fixtures.
3. **MET.** The decision surface renders drafting records (§4). The
   `drafting_context` schema is registered, the review page renders it
   as its own section (what the author read, origin classes, taint
   union, charter hash), submission recomputes the record's flags
   deterministically, and fixture 28 renders a real MOCK-LLM-authored
   proposal end to end (banner, read-beside-wrote, taint). Fixture 20
   proves a tainted record suspends with a loud banner and no approve
   button.
4. Acceptance fixtures for the author frame itself, deterministic, with
   a MOCK model, against the real author path
   (`packs/evolution/author.py`):
   - fixture 25: assembly is four §3 sections and nothing else; a
     planted memory, profile goal, tool output, prior rationale, and
     the exception message all provably never reach the frame; it
     admits exactly the §3 set.
   - fixture 26: pack-owned name (`agent_` prefix) and provenance, the
     model returns source bodies only, no-tools by construction (the
     frame is pure data), the charter can never be authored, the
     charset fold excludes a prose field at assembly, and the authored
     pack passes a real subprocess trial.
   - fixture 27: a tainted CONTEXT suspends even when the mock OUTPUT is
     pristine; the three §5 caps (one-in-flight, daily, no-redraft).
   - fixtures 20-23 (the four enforced folds) hold on the direct path;
     26-27 confirm they hold under the real author.
6. **MET.** Rate/budget caps (§5): fixture 27 covers one draft in
   flight per gap, the daily cap (`max_drafts_per_day`), and the
   no-redraft property.

**Gate 5 (the soak) is the remaining blocker.** Until it finishes green,
the author runs against a MOCK model only. Live-model operation on a
credentialed machine is gated on that green finish, no substitutions.

## 8. Open questions

Two of the three are now closed by the review (§3b, §3a); one remains.

- **CLOSED (review): exception message.** Whether (b) admits a
  sanitized exception message is answered NO for v1 (§3b). The message
  embeds attacker input by construction and has no honest "sanitized"
  form; type and frame names cross, the message does not, and
  debuggability lives on the governed decision surface.
- **CLOSED (review): charter evolution.** The charter is human-PR-only,
  a reserved authored path enforced at the first gate (§3a), and
  charter improvement is permanently out of scope for any autonomous
  loop. The "by hand only, presumably" is now a gate, not a
  presumption.
- **OPEN: multi-turn drafting with the OWNER in the loop** (owner reads
  a draft, replies, author revises): each turn re-runs §3 assembly and
  appends to the drafting record, which the design supports in
  principle, and v1 should still ship single-shot.
