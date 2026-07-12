"""Deterministic conversation content selection before model interpretation."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


FILTER_VERSION = "communication.hygiene@0.1.0"

_QUOTED_START = re.compile(r"^\s*On .{0,240}wrote:\s*$", re.IGNORECASE)
_FORWARDED_START = re.compile(
    r"^\s*-{2,}\s*(original|forwarded) message\s*-{2,}\s*$",
    re.IGNORECASE,
)
_SIGNATURE = re.compile(r"^\s*(--\s*|sent from my\b)", re.IGNORECASE)
_BOILERPLATE = re.compile(
    r"\b(unsubscribe|manage (your )?preferences|view (this )?(email )?in (your )?browser|"
    r"privacy policy|email preferences)\b",
    re.IGNORECASE,
)
_TRACKING = re.compile(
    r"<img\b[^>]*(?:width=[\"']?1|height=[\"']?1|tracking)[^>]*>",
    re.IGNORECASE,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HygieneResult:
    display_content: str
    interpretation_content: str
    interpretation_state: str
    suppression_counts: dict[str, int]
    selections: list[dict[str, int | str]]
    injection_flags: list[str]
    truncated: bool


def select_conversation_content(
    body: str,
    *,
    evidence_offset: int = 0,
    injection_flags: list[str] | None = None,
    notification: bool = False,
    max_selected_chars: int = 12_000,
) -> HygieneResult:
    """Return display text plus exact evidence selectors for interpretation.

    Display text remains inert data for typed clients. Model-eligible text is
    represented as exact contiguous selectors into the authoritative evidence;
    no cleaned/paraphrased copy can become an annotation anchor.
    """

    counts = {
        "quoted_history": 0,
        "signature": 0,
        "boilerplate": 0,
        "tracking": 0,
        "notification": 0,
        "injection": len(injection_flags or []),
    }
    kept: list[tuple[int, int, str]] = []
    cursor = 0
    stopped = False
    for raw_line in body.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        start = cursor
        cursor += len(raw_line)
        if stopped:
            counts["quoted_history"] += bool(line.strip())
            continue
        if _QUOTED_START.match(line) or _FORWARDED_START.match(line):
            counts["quoted_history"] += 1
            stopped = True
            continue
        if line.lstrip().startswith(">"):
            counts["quoted_history"] += 1
            continue
        if _SIGNATURE.match(line):
            counts["signature"] += 1
            stopped = True
            continue
        if _BOILERPLATE.search(line):
            counts["boilerplate"] += 1
            continue
        if _TRACKING.search(line):
            counts["tracking"] += 1
            continue
        if line.strip():
            left = len(line) - len(line.lstrip())
            right = len(line.rstrip())
            exact = line[left:right]
            kept.append((start + left, start + right, exact))

    display = "\n".join(exact for _start, _end, exact in kept).strip()
    if notification:
        counts["notification"] = 1
        return HygieneResult(display, "", "suppressed", counts, [], list(injection_flags or []), False)
    if injection_flags:
        return HygieneResult(display, "", "held", counts, [], list(injection_flags), False)
    if not display:
        return HygieneResult("", "", "empty", counts, [], [], False)

    selections: list[dict[str, int | str]] = []
    selected: list[str] = []
    remaining = max_selected_chars
    truncated = False
    for start, end, exact in kept:
        if remaining <= 0:
            truncated = True
            break
        chosen = exact[:remaining]
        if len(chosen) < len(exact):
            truncated = True
        if chosen:
            selections.append(
                {
                    "start": evidence_offset + start,
                    "end": evidence_offset + start + len(chosen),
                    "exact_hash": _sha(chosen),
                }
            )
            selected.append(chosen)
            remaining -= len(chosen)
        if truncated:
            break
    interpretation = "\n".join(selected)
    return HygieneResult(
        display,
        interpretation,
        "ready" if interpretation else "empty",
        counts,
        selections,
        [],
        truncated,
    )


__all__ = ["FILTER_VERSION", "HygieneResult", "select_conversation_content"]
