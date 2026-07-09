"""The LLM author (docs/llm-author-design.md). MOCK MODEL ONLY.

This is the drafting half of the evolution loop: read a `capability_gap`,
assemble an origin-classified context, call a model once, and submit the
result through `submit_proposal_fn` like any other author. The design's
whole point is the INPUT side, so this module is mostly assembly and
enforcement, and the model is a thin injected callable.

HARD RULE, enforced by construction and by the caller's discipline: the
`model` passed here is a MOCK in every fixture and every test. Pointing
this at a live model on a credentialed machine is gated on the soak
finishing green (design gate 5). Keyless is load-bearing: it is what
keeps building and proving the author safe before the soak clears.

What this module owns, so the model cannot:
  * the pack NAME (an `agent_` prefix the model never sees),
  * `authored_by` (`llm`, stamped by pack code),
  * the manifest and its provenance block,
  * `__init__.py`, the fixtures, and the pinned trial driver.
The model produces exactly four source bodies (object types, behaviors,
settings, tools) and nothing else. It is handed pure data (the frame),
holds zero gateway capabilities, runs once, and returns source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from activegraph.packs.manifest import compute_content_hash

from . import analysis
from .author_frame import (
    ADMISSIBLE_FIELD_PATHS,
    AUTHOR_CHARTER_FILENAME,
    charter_text_and_hash,
    validate_structured_fields,
)
from .materialize import write_files
from .settings import EvolutionSettings
from .tools import submit_proposal_fn
from .trial_driver import TRIAL_DRIVER_PATH, render_trial_driver

# Non-terminal proposal states: a gap with a proposal in one of these
# has a draft in flight, so the one-in-flight cap refuses a new one.
_IN_FLIGHT_STATES = ("drafted", "gated", "trialed", "pending_approval",
                     "adopting", "conflict", "suspended")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------- the model


@dataclass(frozen=True)
class AuthoredSource:
    """The ONLY thing a model returns: the four source bodies. No name,
    no manifest, no provenance, no __init__, no fixtures. Fixed export
    symbols per the charter: OBJECT_TYPES / RELATION_TYPES, BEHAVIORS,
    PackSettings, TOOLS."""

    object_types_src: str
    behaviors_src: str
    settings_src: str
    tools_src: str


# A model is any callable from a pure-data frame to AuthoredSource. The
# frame carries NO graph handle and NO gateway capability, so "no tools
# during drafting" holds by construction: there is nothing to call.
Model = Callable[[dict], AuthoredSource]


class AuthorRefusal(Exception):
    """The author refused to draft (a cap, a gate, or a tainted frame).
    Refusals are outcomes, surfaced to the caller, never silent."""


# ---------------------------------------------------------- §3 assembly


def _verified_owner_inputs(graph, gateway_settings) -> list:
    """§3d: chat_input objects whose sender resolves to a verified
    principal holding an approver role. The one free-text origin."""
    from packs.identity_auth.tools import lookup_principal_fn

    approver_roles = set(getattr(gateway_settings, "approver_roles",
                                 ("owner", "admin")))
    out = []
    try:
        inputs = list(graph.objects(type="chat_input"))
    except Exception:
        return out
    for ci in inputs:
        sender = (ci.data or {}).get("user_ref") or ""
        principal = lookup_principal_fn(graph, sender) if sender else None
        if principal and principal.get("role") in approver_roles:
            out.append(ci)
    return out


def assemble_frame(graph, gap_id: str, settings: EvolutionSettings, *,
                   gateway_settings) -> tuple[dict, dict]:
    """Build the four-section frame by pack code (§3), deterministic.

    Returns (frame, admitted). The frame is PURE DATA handed to the
    model: charter, the gap's structured fields (values, charset-clean),
    the target surface from source of truth, and verified-owner text
    wrapped in the external-content envelope. Nothing else crosses:
    memory, profiles, capability_result.output_data, web/MCP text,
    unverified-sender messages, and prior proposals' rationales are
    never read here, so they cannot enter.

    `admitted` is the drafting-record skeleton: the exact object ids and
    field paths that crossed, for the taint recompute at submission."""
    from packs.tool_gateway.untrusted import scan_for_injection, wrap_untrusted

    gap = graph.get_object(gap_id)
    if gap is None or gap.type != "capability_gap":
        raise AuthorRefusal(f"no capability_gap {gap_id!r}")

    # (a) Charter: the one fully-trusted origin, hash-pinned.
    charter, charter_hash = charter_text_and_hash()

    # (b) Structured gap fields ONLY (§3b): admissible field paths whose
    # values pass the identifier charset. The exception MESSAGE never
    # crosses; only exception_type (an identifier) is admissible.
    structured_fields: list[str] = []
    gap_fields: dict[str, Any] = {}
    evidence_ids = list((gap.data or {}).get("evidence_refs") or [])
    for ev_id in evidence_ids:
        obj = graph.get_object(str(ev_id))
        if obj is None:
            continue
        for field_path in sorted(ADMISSIBLE_FIELD_PATHS):
            if field_path not in (obj.data or {}):
                continue
            entry = f"{obj.id}:{field_path}"
            if validate_structured_fields(graph, [entry]):
                continue  # fails charset: not a structured identifier
            structured_fields.append(entry)
            gap_fields[entry] = obj.data[field_path]

    # (c) Target surface from source of truth (§3c): loaded pack
    # declarations from the graph's pack.loaded events, and object-type
    # names. Repo-shipped / loader-introspected, never conversational.
    surface_sources: list[str] = []
    surface: dict[str, Any] = {"packs": [], "object_types": []}
    try:
        for e in graph.events:
            if e.type == "pack.loaded":
                name = (e.payload or {}).get("name", "")
                if name and name not in surface["packs"]:
                    surface["packs"].append(name)
                    surface_sources.append(f"pack:{name}")
                for ot in (e.payload or {}).get("object_types") or []:
                    if ot not in surface["object_types"]:
                        surface["object_types"].append(ot)
    except Exception:
        pass

    # (d) Verified-owner text (§3d): the one free-text origin, wrapped in
    # the external-content envelope and scanned (the tripwire flags the
    # record, never blocks).
    owner_input_ids: list[str] = []
    owner_text: list[str] = []
    record_flags: set[str] = set()
    for ci in _verified_owner_inputs(graph, gateway_settings):
        owner_input_ids.append(str(ci.id))
        content = str((ci.data or {}).get("content", ""))
        hits = scan_for_injection(content)
        record_flags.update(hits)
        record_flags.update((ci.data or {}).get("injection_flags") or [])
        owner_text.append(wrap_untrusted(content, list(hits)))

    frame = {
        "charter": charter,
        "gap_fields": gap_fields,          # values only, charset-clean
        "surface": surface,
        "owner_text": owner_text,          # envelope-wrapped
    }
    admitted = {
        "charter_hash": charter_hash,
        "gap_id": str(gap.id),
        "structured_fields": structured_fields,
        "surface_sources": surface_sources,
        "owner_input_ids": owner_input_ids,
        "injection_flags": sorted(record_flags),
    }
    return frame, admitted


# --------------------------------------------------- pipeline assembly

_INIT_SRC = '''"""{name}: an agent-authored candidate pack."""

from __future__ import annotations

from activegraph.packs import Pack

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import PackSettings

pack = Pack(
    name="{name}",
    version="0.1.0",
    description="Agent-authored candidate pack.",
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=(),
    policies=(),
    prompts=(),
    settings_schema=PackSettings,
)
'''

_FIXTURES_SRC = '''"""Self-contained fixtures for {name}."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from activegraph import Graph, Runtime

from {name} import pack


def run_all() -> bool:
    rt = Runtime(Graph())
    rt.load_pack(pack)
    rt.run_until_idle()
    return True


def main(rt):
    assert run_all()


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
'''

_MANIFEST_SRC = '''[pack]
name = "{name}"
version = "0.1.0"
description = "Agent-authored candidate pack."
license = "Apache-2.0"

[pack.provenance]
authors = ["llm author"]
authored_by = "agent"
generator = "packs/evolution/author.py"
source_url = ""
created_at = "2026-07-08T00:00:00Z"

[pack.integrity]
content_hash = "{content_hash}"

[dependencies]
activegraph = ">=1.7.1,<2.0"
python = ">=3.11"
python-deps = []

[surface]
object_types = {object_types}
relation_types = {relation_types}
behaviors = {behaviors}
tools = {tools}
settings_schema = "PackSettings"

[fixtures]
entrypoint = "fixtures/run_fixtures.py"
deterministic = true
'''


def _assemble_files(source: AuthoredSource, pack_name: str,
                    settings: EvolutionSettings) -> dict[str, str]:
    """Wrap the model's four source bodies into a full pack. Pack code
    owns __init__, the fixtures, the pinned trial driver, and (below)
    the manifest with its provenance: the model touches none of them."""
    files = {
        "__init__.py": _INIT_SRC.format(name=pack_name),
        "object_types.py": source.object_types_src,
        "behaviors.py": source.behaviors_src,
        "settings.py": source.settings_src,
        "tools.py": source.tools_src,
        "fixtures/run_fixtures.py": _FIXTURES_SRC.format(name=pack_name),
        TRIAL_DRIVER_PATH: render_trial_driver(settings),
    }
    surface = analysis.extract_surface(files)
    root = write_files(files, pack_name=pack_name)
    content_hash = compute_content_hash(root)
    files["manifest.toml"] = _MANIFEST_SRC.format(
        name=pack_name, content_hash=content_hash,
        object_types=sorted(surface["object_types"]),
        relation_types=sorted(surface["relation_types"]),
        behaviors=sorted(surface["behaviors"]),
        tools=sorted(surface["tools"]))
    return files


# --------------------------------------------------- caps and the draft


def _drafts_today(graph, day: str) -> int:
    n = 0
    try:
        for r in graph.objects(type="drafting_context"):
            if (r.data or {}).get("day") == day:
                n += 1
    except Exception:
        pass
    return n


def _proposal_in_flight_for_gap(graph, gap_id: str) -> bool:
    try:
        for p in graph.objects(type="mod_proposal"):
            if (p.data or {}).get("gap_id") == gap_id and \
                    (p.data or {}).get("status") in _IN_FLIGHT_STATES:
                return True
    except Exception:
        pass
    return False


def draft_proposal(graph, *, gap_id: str, model: Model,
                   settings: EvolutionSettings, gateway_settings,
                   base_name: str = "pack", today: Optional[str] = None):
    """Draft one candidate pack for a gap, MOCK MODEL only (§3, §5).

    One shot: assemble the frame, seal the drafting record, call the
    model once with pure data (no tools), take its four source bodies,
    stamp name and provenance in pack code, and submit. Refuses (raises
    AuthorRefusal) when a cap trips: one draft in flight per gap, the
    daily draft cap, and no redraft loop (assembly never reads a prior
    rejection, so a retry is a fresh draft from the same context, and a
    still-in-flight gap is simply not redrafted).

    Returns the submitted mod_proposal object."""
    today = today or _now()[:10]

    # Cap: one draft in flight per gap (also the no-redraft guard: a
    # gap whose proposal is still in flight is not redrafted).
    if _proposal_in_flight_for_gap(graph, gap_id):
        raise AuthorRefusal(
            f"a proposal for gap {gap_id!r} is already in flight; one "
            "draft per gap at a time")
    # Cap: daily draft budget.
    if _drafts_today(graph, today) >= settings.max_drafts_per_day:
        raise AuthorRefusal(
            f"daily draft cap reached ({settings.max_drafts_per_day}); "
            "no more drafts today")

    frame, admitted = assemble_frame(graph, gap_id, settings,
                                     gateway_settings=gateway_settings)

    # Seal the drafting record BEFORE the model call (§4). The model's
    # output cannot patch it; taint is recomputed from admitted ids at
    # submission, never from this stored flags field.
    record = graph.add_object("drafting_context", {
        "charter_hash": admitted["charter_hash"],
        "gap_id": admitted["gap_id"],
        "structured_fields": admitted["structured_fields"],
        "surface_sources": admitted["surface_sources"],
        "owner_input_ids": admitted["owner_input_ids"],
        "injection_flags": admitted["injection_flags"],
        "model": "llm:mock",
        "day": today,
        "at": _now(),
    })

    # The one model call. Pure data in, source out. No graph, no tools.
    source = model(frame)
    if not isinstance(source, AuthoredSource):
        raise AuthorRefusal(
            "model must return AuthoredSource (four source bodies), not "
            f"{type(source).__name__}")

    # Pack code owns the name (agent_ prefix; the model never saw it) and
    # every provenance byte.
    pack_name = f"agent_{base_name}"
    files = _assemble_files(source, pack_name, settings)

    # A model that tried to smuggle the charter path (or any non-source
    # file) simply cannot: it returns four strings, and _assemble_files
    # writes the fixed file set. Assert the invariant loudly anyway.
    assert AUTHOR_CHARTER_FILENAME not in files, "charter is never authored"

    return submit_proposal_fn(
        graph, pack_name=pack_name, files=files, gap_id=gap_id,
        rationale="", authored_by="llm", drafting_context_id=str(record.id))
