# LLM Author: design

**Status: DESIGN ONLY. The author is unbuilt on purpose. The evolution
pipeline stays scripted-author-only until BOTH gates below hold: the
runtime ships subprocess isolation for fork trials (evolution-design
§7.2), AND this design has survived a review by someone who did not
write it. Building this from a prompt drafted in an afternoon is the
single riskiest move available to this codebase, which is why the
design exists before the code.**

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

**(b) The gap, structured.** From the `capability_gap` and its
evidence chain, STRUCTURED FIELDS ONLY: capability name, provider,
risk class, exception_type, failure counts, the object types involved.
The free-text fields (`description`, `capability_result.output_data`,
message bodies, tracebacks beyond the exception type and frame names)
do not cross. A traceback quotes the failing input; the failing input
is the attacker's text in the tool-failure case. If the owner wants
the author to see prose about the gap, the owner writes that prose
(section d).

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
(d), and the injection flags found on any of them. The proposal then
inherits `injection_flags` as the union over every admitted object,
the same deterministic rule gaps use, computed from the drafting
record rather than from anything the model reports. A tainted context
yields a suspended proposal even when the model output looks pristine,
and the decision surface renders the drafting record next to the diff
so the owner sees what the author read, never just what it wrote.

## 5. The author frame's mechanics

- **One shot, no tools.** The author gets the assembled context and
  returns files. It holds zero gateway capabilities during drafting:
  no search, no memory recall, no fetch. Every tool call during
  drafting is a channel for exactly the text §3 excluded, so the
  drafting frame has no tool surface at all. If drafting needs more
  context, that is a gap in (b) or (c) assembly, fixed in pack code
  where it is reviewable.
- **Output is files, nothing else.** The model returns the authored
  file set; pack code writes the manifest scaffold, computes the
  content hash with the runtime's implementation, and calls
  `submit_proposal_fn` with `authored_by="llm"`. The model never picks
  the pack name (namespaced `agent_` prefix, reserved-namespace gate
  unchanged), never sets `authored_by`, never touches the manifest's
  provenance block.
- **The decision surface shouts.** `authored_by="llm"` renders the
  same AGENT banner, plus the drafting record: charter hash, admitted
  ids, flags. An owner approving an LLM-authored pack sees what it
  read and what it wrote, on one page.
- **Budget and rate.** At most one draft in flight per gap, a daily
  cap on drafts (setting), and no redrafting loops that feed a
  rejection's gate details back into a fresh context. Gate details
  quote candidate source; candidate source is model text; feeding it
  back builds an unreviewed self-conversation. A rejected proposal's
  retry is a fresh draft from the same (a) through (d) context, plus
  the owner's own words if the owner wants to steer.

## 6. What this does NOT claim

- It does not claim the model cannot write a malicious pack from a
  clean context. The gates, the trial, the pins, and a reading owner
  remain the containment for that, and the size caps exist so the
  reading is real.
- It does not claim the regex tripwire catches injections. Origin
  classification is the control; the tripwire is an audit trail.
- It does not claim structured fields are unpoisonable. A capability
  NAME could in principle carry a payload. Structured admission
  shrinks the channel from prose to identifiers; it does not zero it.
- It does not cover a hostile owner. A verified approver steering the
  author toward a bad pack is out of scope by definition: that person
  already holds approval power.

## 7. Gate conditions to build (all of them, no substitutions)

1. Runtime subprocess isolation for fork trials shipped and consumed
   here, so a hostile candidate's first execution happens outside the
   parent process (today's fixture gate subprocess covers fixtures
   only; in-process replay is evolution-design T5's accepted surface
   and stops being acceptable the day an adversarial author exists).
2. This design reviewed by someone who did not write it, with the
   review recorded in this file's history.
3. The decision surface renders drafting records (§4), so LLM-authored
   proposals are reviewable to the same depth as their diffs. BUILT
   AHEAD of the author: the drafting_context schema is registered, the
   review page renders it as its own section (what the author read,
   origin classes, taint union, charter hash), submission inherits the
   record's flags deterministically, and fixture 20 proves a tainted
   record suspends with a loud banner and no approve button. When the
   author lands, this gate is a wiring step.
4. Acceptance fixtures for the author frame itself, deterministic,
   with a mock model: context assembly admits exactly the §3 set, a
   flagged owner input taints the proposal, a memory object in the
   graph provably never reaches the frame, the drafting record is
   complete, and the no-tools property holds by construction.

Until then: scripted author, owner-drafted packs, and nothing else.

## 8. Open questions, stated instead of hidden

- Whether (b) should admit a bounded, sanitized exception MESSAGE (not
  the traceback) for debuggability, and what "sanitized" would honestly
  mean for text that embeds attacker input by construction.
- Charter evolution: the charter is hash-pinned per draft, and how the
  charter itself gets improved (by hand only, presumably through the
  same PR review as any repo file) deserves an explicit rule before
  anyone is tempted to let the system tune its own author.
- Multi-turn drafting with the OWNER in the loop (owner reads a draft,
  replies, author revises): each turn re-runs §3 assembly and appends
  to the drafting record, which the design supports in principle, and
  v1 should still ship single-shot.
