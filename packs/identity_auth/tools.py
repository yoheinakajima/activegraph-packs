"""Identity/Auth Pack tools — v0.1.

Provides helpers for principal lookup and permission checking that
can be called from outside behavior chains.
"""

from __future__ import annotations

from typing import Any, Optional

from activegraph.packs import tool


# ------------------------------------------------------------------ raw functions


def lookup_principal_fn(
    graph: Any,
    sender_ref: str,
) -> Optional[dict]:
    """Find the most recent principal matching a sender_ref.

    Scans graph principals for a matching identifiers.ref value.
    Returns the principal data dict or None if not found.

    Note: In behaviors, use graph.get_object(id) instead.
    This function is safe to call outside behavior context.
    """
    try:
        for obj in graph.objects(type="principal"):
            if obj.data.get("identifiers", {}).get("ref") == sender_ref:
                return {"id": obj.id, **obj.data}
    except Exception:
        pass
    return None


def check_principal_permission_fn(
    graph: Any,
    principal_id: str,
    action: str,
    risk_class: str = "medium",
) -> dict:
    """Check whether a principal has permission for an action.

    Returns a dict with keys:
        allowed (bool), role (str), reason (str)
    """
    from .object_types import Principal as PrincipalModel

    try:
        obj = graph.get_object(principal_id)
        if not obj:
            return {"allowed": False, "role": "unknown", "reason": "principal_not_found"}

        role = obj.data.get("role", "unknown")

        if role == "blocked":
            return {"allowed": False, "role": role, "reason": "principal_blocked"}

        if risk_class in ("high", "critical") and role not in ("owner", "admin"):
            return {
                "allowed": False,
                "role": role,
                "reason": f"risk_class_{risk_class}_requires_owner_or_admin",
            }

        p = PrincipalModel(name="", role=role)
        if p.can(action):
            return {"allowed": True, "role": role, "reason": "role_allows"}

        return {"allowed": False, "role": role, "reason": "insufficient_role"}

    except Exception as exc:
        return {"allowed": False, "role": "unknown", "reason": f"error: {exc}"}


def register_principal_fn(
    graph: Any,
    sender_ref: str,
    role: str,
    name: Optional[str] = None,
    channel: str = "unknown",
) -> dict:
    """Create or promote a Principal for *sender_ref* with an explicit role.

    The explicit counterpart to the automatic resolvers (which assign
    default_external_role to unknown senders): use it to seed the owner at
    build time or to promote a contact to collaborator/admin. Idempotent —
    an existing principal for the same normalized ref is patched, not
    duplicated, and the in-process dedup registry is kept in sync so the
    resolvers and reply gating see the change immediately.

    Returns {"principal_id": ..., "role": ..., "created": bool}.
    """
    from datetime import datetime, timezone

    from .behaviors import _PRINCIPAL_REGISTRY, _normalize_identifier

    now = datetime.now(timezone.utc).isoformat()
    norm = _normalize_identifier(sender_ref)

    existing_id = _PRINCIPAL_REGISTRY.get(norm)
    if existing_id:
        try:
            graph.patch_object(existing_id, {"role": role, "last_seen_at": now})
        except Exception:
            pass
        return {"principal_id": existing_id, "role": role, "created": False}

    identifiers: dict[str, str] = {"ref": sender_ref}
    if "@" in sender_ref:
        identifiers["email"] = sender_ref
    principal = graph.add_object("principal", {
        "name": name or sender_ref,
        "role": role,
        "auth_confidence": 1.0,   # explicit registration, not inference
        "identifiers": identifiers,
        "channel": channel,
        "last_seen_at": now,
        "seen_count": 1,
        "metadata": {"registered_by": "register_principal"},
    })
    _PRINCIPAL_REGISTRY[norm] = principal.id
    return {"principal_id": principal.id, "role": role, "created": True}


# ------------------------------------------------------------------ tool wrappers


@tool(
    name="lookup_principal",
    description=(
        "Find an existing principal by sender_ref (email, Slack ID, phone, etc.). "
        "Returns the principal data dict or None if not found."
    ),
)
def lookup_principal(graph: Any, sender_ref: str) -> Optional[dict]:
    """Registered tool wrapper — delegates to lookup_principal_fn."""
    return lookup_principal_fn(graph, sender_ref)


@tool(
    name="check_principal_permission",
    description=(
        "Check whether a principal has permission for an action. "
        "Returns {allowed, role, reason}."
    ),
)
def check_principal_permission(
    graph: Any,
    principal_id: str,
    action: str = "",
    risk_class: str = "medium",
) -> dict:
    """Registered tool wrapper — delegates to check_principal_permission_fn."""
    return check_principal_permission_fn(graph, principal_id, action, risk_class)


@tool(
    name="register_principal",
    description=(
        "Create or promote a Principal with an explicit role (e.g. seed the "
        "owner, promote a contact to collaborator). Idempotent by normalized "
        "sender_ref. Returns {principal_id, role, created}."
    ),
)
def register_principal(
    graph: Any,
    sender_ref: str,
    role: str = "",
    name: Optional[str] = None,
    channel: str = "unknown",
) -> dict:
    """Registered tool wrapper — delegates to register_principal_fn."""
    return register_principal_fn(graph, sender_ref, role, name, channel)


TOOLS = [lookup_principal, check_principal_permission, register_principal]
