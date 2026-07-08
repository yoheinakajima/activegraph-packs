"""Author-frame assembly enforcement (docs/llm-author-design.md §3-§4).

The LLM author itself is unbuilt (its build gates are the design review,
now complete, plus a green soak). These are the primitives that turn the
design's asserted trust boundaries into ENFORCED ones: the taint
recompute that a corrupted drafting record cannot launder, and the
structured-field charset check that keeps "structured" from meaning
"prose we are calling structured". They are callable today (by the
scripted paths and, later, by the author's frame-assembly code) and
fixture-tested now, so the author build is a wiring step, never a
trust-boundary invention.

Nothing here reads or writes candidate code, imports anything, or
touches a model. Pure graph reads and regex.
"""

from __future__ import annotations

import re
from typing import Any

# The one repo path an authored pack may never target. The charter is
# the author's system prompt: the single fully-trusted origin in the
# frame (§3a). Its trust is human-PR-only, so it is a reserved authored
# path in the same family as the reserved capability namespaces, and
# charter improvement is permanently out of scope for any autonomous
# loop, not just v1 (§8, now a gate).
AUTHOR_CHARTER_FILENAME = "author_charter.md"

# The manifest identifier charset (activegraph.packs.manifest._NAME_RE),
# pinned here as a constant so the evolution pack validates admitted
# structured identifiers against the exact shape the runtime enforces on
# pack, provider, and capability names.
_MANIFEST_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

# A dotted Python identifier: RuntimeError,
# activegraph.errors.PackConflictError. No spaces, no punctuation prose
# can hide in, so an exception_type field cannot smuggle a sentence.
_EXCEPTION_TYPE_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")

_RISK_CLASSES = frozenset({"low", "medium", "high", "critical"})


def _is_name(v: Any) -> bool:
    return isinstance(v, str) and bool(_MANIFEST_NAME_RE.match(v))


def _is_exception_type(v: Any) -> bool:
    return isinstance(v, str) and len(v) <= 128 and bool(
        _EXCEPTION_TYPE_RE.match(v))


def _is_count(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


# The CLOSED set of field paths §3b admits from a gap's evidence chain,
# each bound to the charset that keeps it an identifier. A field path
# not in this map is not admissible as structured evidence at all: the
# allow-list IS the "structured fields only" rule.
_FIELD_VALIDATORS = {
    "provider_name": _is_name,
    "capability_name": _is_name,
    "risk_class": lambda v: v in _RISK_CLASSES,
    "exception_type": _is_exception_type,
    "failure_count": _is_count,
    "n": _is_count,
}

ADMISSIBLE_FIELD_PATHS = frozenset(_FIELD_VALIDATORS)


def validate_structured_fields(graph, structured_fields: list[str]) -> list[str]:
    """Charset-check every admitted structured field (§3b/§6).

    Each entry is ``"<object_id>:<field_path>"``. A field is admissible
    only when its field path is in the §3b allow-list AND the value it
    references on the graph object matches that field's charset. A
    capability_name that reads like a sentence, an exception_type with
    spaces, a count that is a string: all rejected here, so the residual
    "a capability NAME could carry a payload" channel (§6) is bounded to
    identifiers rather than left as prose. Returns violation strings;
    empty means clean."""
    violations: list[str] = []
    for entry in structured_fields:
        object_id, sep, field_path = str(entry).partition(":")
        if not sep or not field_path:
            violations.append(
                f"structured field {entry!r} is not '<object_id>:<field_path>'")
            continue
        validator = _FIELD_VALIDATORS.get(field_path)
        if validator is None:
            violations.append(
                f"{entry}: field path {field_path!r} is not admissible as "
                f"structured evidence (allowed: "
                f"{', '.join(sorted(ADMISSIBLE_FIELD_PATHS))})")
            continue
        obj = graph.get_object(object_id)
        if obj is None:
            violations.append(f"{entry}: object {object_id!r} not in the graph")
            continue
        value = (obj.data or {}).get(field_path)
        if not validator(value):
            violations.append(
                f"{entry}: value {value!r} fails the {field_path!r} charset "
                f"(prose-shaped text is not a structured identifier)")
    return violations


def _admitted_object_ids(record_data: dict[str, Any]) -> list[str]:
    """Every GRAPH object a drafting record admitted: the objects behind
    the structured fields (§3b), the owner inputs (§3d), and the gap.
    Surface sources (§3c) are repo-shipped paths, not graph objects, so
    they carry no taint and are not consulted."""
    ids: list[str] = []
    for entry in record_data.get("structured_fields") or []:
        object_id = str(entry).partition(":")[0]
        if object_id:
            ids.append(object_id)
    ids.extend(str(i) for i in (record_data.get("owner_input_ids") or []))
    gap_id = record_data.get("gap_id")
    if gap_id:
        ids.append(str(gap_id))
    return ids


def recompute_drafting_taint(graph, record_data: dict[str, Any]) -> list[str]:
    """The §4 analog of gap-lineage's deterministic taint union.

    Recomputes the injection-flag union over every object the drafting
    record ADMITTED, read fresh from the graph by id, ignoring any
    ``injection_flags`` value the record itself stores. The record is
    sealed before the model call and the model's output cannot patch it;
    even so, taint enforcement never trusts a stored field, so a
    record-corruption bug cannot launder taint. Returns the sorted
    union."""
    flags: set[str] = set()
    for object_id in _admitted_object_ids(record_data):
        obj = graph.get_object(object_id)
        if obj is not None:
            flags.update((obj.data or {}).get("injection_flags") or [])
    return sorted(flags)
