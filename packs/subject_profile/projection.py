"""Owner alias set — the queryable projection over confirmed subject facts.

ADR 0039: interpretation is owner-anchored. This is a pure, deterministic,
horizon-stable function of the reader's promoted ``subject_fact`` state —
additions and removals follow fact supersession, whatever flow minted the
fact (bootstrap confirmations qualify exactly like connector-era reviews).

Only ``addresses`` (plus the connected ``account_refs`` a caller supplies)
identify the owner as a conversation participant. ``domains`` and ``handles``
name the owner's confirmed web presence — a colleague on the owner's domain
is not the owner.
"""

from __future__ import annotations

from typing import Any, Iterable


ALIAS_ATTRIBUTES = ("email", "handle", "url")


def _domain_of(value: str) -> str:
    candidate = value.strip().lower()
    for prefix in ("https://", "http://", "//"):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix):]
            break
    candidate = candidate.split("/", 1)[0].split("?", 1)[0].split("@")[-1]
    if candidate.startswith("www."):
        candidate = candidate[4:]
    return candidate if "." in candidate else ""


def owner_alias_set_fn(
    reader,
    *,
    subject_ref: str = "owner",
    account_refs: Iterable[str] = (),
) -> dict[str, Any]:
    """Addresses, handles, and domains the owner has explicitly confirmed."""
    addresses = {
        str(ref).strip().lower() for ref in account_refs if str(ref).strip()
    }
    handles: set[str] = set()
    domains: set[str] = set()
    fact_refs: list[str] = []
    for fact in reader.objects(type="subject_fact"):
        data = fact.data or {}
        if data.get("subject_ref") != subject_ref:
            continue
        if data.get("status") != "promoted":
            continue
        attribute = str(data.get("attribute") or "")
        value = str(data.get("value") or "").strip().lower()
        if not value or attribute not in ALIAS_ATTRIBUTES:
            continue
        if attribute == "email" and "@" in value:
            addresses.add(value)
        elif attribute == "handle":
            handles.add(value.lstrip("@"))
        elif attribute == "url":
            domain = _domain_of(value)
            if not domain:
                continue
            domains.add(domain)
        fact_refs.append(str(data.get("fact_identity") or fact.id))
    return {
        "subject_ref": subject_ref,
        "addresses": sorted(addresses),
        "handles": sorted(handles),
        "domains": sorted(domains),
        "fact_refs": sorted(set(fact_refs)),
        "basis": "confirmed_subject_facts",
    }


__all__ = ["ALIAS_ATTRIBUTES", "owner_alias_set_fn"]
