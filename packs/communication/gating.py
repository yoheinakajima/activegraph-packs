"""Reply gating — identity on the respond path.

One function, one policy decision, shared by every channel adapter: may
this sender receive a full conversational reply? Adapters (chat, and the
messaging adapters built on the same pattern) call ``decide_reply`` at
INGESTION time and stamp the verdict onto the ``comm_message`` they create,
so responders can match it declaratively in their ``where`` clause — which
is what lets an LLM responder skip the (paid) model call entirely for gated
senders instead of discovering the gate afterward.

Policies:
  "open"        — everyone gets replies (except blocked principals).
  "known"       — owner / admin / collaborator principals only.
  "owner_only"  — owner / admin principals only.

Fail-closed by design: a restrictive policy with no resolvable principal
deflects. A sender the identity system has never seen — including when the
Identity/Auth Pack isn't loaded at all — is an unrecognized sender, and
restriction that cannot be verified must restrict. Seed the owner up front
(bundles.seed_owner_principals / identity_auth.register_principal) so the
owner's very first message passes.

Deflection is a decision, not silence: the adapter still sends a bounded
template reply and records the gate verdict on graph objects, so a
stranger's experience is polite and the audit trail shows exactly why.
"""

from __future__ import annotations

from typing import Optional

# Roles allowed per policy. "blocked" is denied even under "open".
_ALLOWED_ROLES: dict[str, tuple[str, ...]] = {
    "known": ("owner", "admin", "collaborator"),
    "owner_only": ("owner", "admin"),
}

REPLY_POLICIES = ("open", "known", "owner_only")


def decide_reply(graph, sender_ref: Optional[str], *, reply_policy: str) -> dict:
    """Decide whether *sender_ref* gets a full reply under *reply_policy*.

    Returns ``{"gate": "open"|"deflect", "role": str|None, "reason": str}``.
    The principal lookup is behavior-safe (identity_auth's in-process
    registry + get_object; no graph scans), so adapters may call this from
    inside behaviors.
    """
    if reply_policy not in REPLY_POLICIES:
        # An unknown policy is a misconfiguration — fail closed, say why.
        return {
            "gate": "deflect",
            "role": None,
            "reason": f"unknown reply_policy {reply_policy!r} (use one of {REPLY_POLICIES})",
        }

    principal = None
    try:
        from packs.identity_auth.behaviors import resolve_known_principal

        principal = resolve_known_principal(graph, sender_ref or "")
    except Exception:
        principal = None  # identity_auth not installed/loaded

    role = principal.get("role") if principal else None

    if role == "blocked":
        return {"gate": "deflect", "role": role, "reason": "principal is blocked"}

    if reply_policy == "open":
        return {"gate": "open", "role": role, "reason": "policy is open"}

    allowed = _ALLOWED_ROLES[reply_policy]
    if role in allowed:
        return {"gate": "open", "role": role, "reason": f"role {role!r} allowed by {reply_policy!r}"}

    if principal is None:
        return {
            "gate": "deflect",
            "role": None,
            "reason": (
                f"policy {reply_policy!r} and sender is unrecognized "
                "(no principal — seed the owner via register_principal)"
            ),
        }
    return {
        "gate": "deflect",
        "role": role,
        "reason": f"role {role!r} not allowed by policy {reply_policy!r}",
    }
