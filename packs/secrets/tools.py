"""Secrets Pack tools — v0.1.

Two functions for credential resolution:

1. resolve_credential_fn — pure function, no graph access.
   Resolves credential from environment. Does NOT create usage events.
   Use for unit testing where no graph is available.

2. resolve_and_audit_fn — graph-aware function.
   Resolves credential AND creates a SecretUsageEvent in the graph.
   credential_resolution_recorder behavior fires on the usage event
   and patches CredentialRef.last_used_at and use_count.
   Use this in production behaviors.

SECURITY RULES:
- The resolved secret value is RETURNED to the caller, not stored
- It is NEVER written to the graph, events, logs, or artifacts
- The caller must use it immediately (e.g. as a header) and discard it
- Usage is recorded as a SecretUsageEvent (name only, not value)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from activegraph.packs import tool

from .object_types import SecretUsageEvent


# ------------------------------------------------------------------ raw functions (callable directly)


def resolve_credential_fn(
    credential_name: str,
    env_prefix: str = "",
) -> Optional[str]:
    """Resolve a credential name to its value from environment variables.

    Pure function — no graph access. The returned value must be used
    immediately and not persisted.

    Args:
        credential_name: Name of the credential (used as env var key)
        env_prefix: Optional prefix prepended to the env var name

    Returns:
        The secret value string, or None if not found.
    """
    env_key = f"{env_prefix}{credential_name}".upper()
    return os.environ.get(env_key)


def resolve_and_audit_fn(
    graph: Any,
    credential_name: str,
    env_prefix: str = "",
    behavior_name: Optional[str] = None,
    frame_id: Optional[str] = None,
    call_id: Optional[str] = None,
    credential_ref_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve a credential AND record a SecretUsageEvent in the graph.

    Creates a SecretUsageEvent with event_type='resolved', which triggers
    credential_resolution_recorder to update CredentialRef.last_used_at
    and use_count.

    IMPORTANT: The returned secret value must NOT be stored. Use it
    immediately (as an API header, etc.) and let it go out of scope.

    Args:
        graph: ActiveGraph Graph instance
        credential_name: Name of the credential to resolve
        env_prefix: Optional env var prefix
        behavior_name: Caller name for audit trail
        frame_id: Optional frame scope
        call_id: Optional capability_call_id for credential_used_in relation
        credential_ref_id: Optional ID of the CredentialRef graph object,
                           used by credential_resolution_recorder to patch
                           last_used_at and use_count directly via get_object()

    Returns:
        The secret value string, or None if not found.
    """
    value = resolve_credential_fn(credential_name, env_prefix=env_prefix)
    resolved = value is not None

    now = datetime.now(timezone.utc).isoformat()

    # Create SecretUsageEvent
    # credential_resolution_recorder fires on this and patches CredentialRef
    usage_event = graph.add_object(
        "secret_usage_event",
        SecretUsageEvent(
            credential_ref_name=credential_name,
            behavior_name=behavior_name,
            resolved=resolved,
            frame_id=frame_id,
            timestamp=now,
            metadata={
                "event_type": "resolved",
                "found_in_env": resolved,
                "credential_ref_id": credential_ref_id or "",
            },
        ).model_dump(),
    )

    # If call_id provided, create credential_used_in relation
    if call_id:
        try:
            graph.add_relation(usage_event.id, call_id, "credential_used_in")
        except Exception:
            pass

    return value  # Caller must use immediately and not persist


# ------------------------------------------------------------------ tool wrapper (for pack registration)


@tool(
    name="resolve_credential",
    description=(
        "Resolve a CredentialRef name to its actual secret value from the environment. "
        "Returns the secret value (use immediately, do not store in graph or logs). "
        "Returns None if the credential is not found. "
        "Use resolve_and_audit_fn() when a graph instance is available for full audit trail."
    ),
)
def resolve_credential(
    credential_name: str,
    env_prefix: str = "",
    behavior_name: Optional[str] = None,
) -> Optional[str]:
    """Registered tool wrapper — resolves credential without graph access."""
    return resolve_credential_fn(credential_name, env_prefix=env_prefix)


TOOLS = [resolve_credential]
