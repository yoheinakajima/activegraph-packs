"""Plan and propose budgeted public-presence fetches through the gateway.

The tool only PROPOSES ``capability_call`` objects (status ``proposed``);
the Tool Gateway's policy_enforcer/call_executor own approval and
execution, so every fetch is a recorded, policy-checked R0 call — this
pack never contacts the network directly.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from activegraph.packs import tool

IMPORTER_ID = "public_presence"
IMPORTER_VERSION = "0.1.0"
DEFAULT_SURFACE_ID = "public_presence"

# Deterministic planning order: identity anchors first.
_HANDLE_ORDER = ("github", "x", "twitter", "site", "urls")


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()}"


def _normalize_handle(value: str) -> str:
    return value.strip().lstrip("@").strip("/")


def _url_for(kind: str, value: str) -> Optional[str]:
    cleaned = _normalize_handle(value)
    if not cleaned:
        return None
    if kind == "github":
        return f"https://github.com/{cleaned}"
    if kind in ("x", "twitter"):
        return f"https://x.com/{cleaned}"
    if kind == "site":
        raw = value.strip()
        if raw.startswith(("http://", "https://")):
            return raw.rstrip("/")
        return f"https://{cleaned}"
    return None


def plan_presence_urls(handles: dict[str, Any]) -> tuple[list[tuple[str, str]], list[dict[str, str]]]:
    """Deterministically map free-text handles to fetchable URLs.

    Returns (planned [(kind, url)], skipped [{key, reason}]). Handle keys
    with no fetch strategy (e.g. ``company`` — a fact, not a page) are
    recorded, never guessed at.
    """
    planned: list[tuple[str, str]] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()

    for kind in _HANDLE_ORDER:
        if kind not in handles:
            continue
        value = handles[kind]
        if kind == "urls":
            values = value if isinstance(value, (list, tuple)) else [value]
            for entry in values:
                raw = str(entry).strip()
                if not raw:
                    continue
                url = raw if raw.startswith(("http://", "https://")) else f"https://{raw}"
                url = url.rstrip("/")
                if url not in seen:
                    seen.add(url)
                    planned.append(("url", url))
            continue
        url = _url_for(kind, str(value)) if value else None
        if url is None:
            skipped.append({"key": kind, "reason": "empty_or_unmappable"})
        elif url in seen:
            skipped.append({"key": kind, "reason": "duplicate_url"})
        else:
            seen.add(url)
            planned.append((kind, url))

    for key in sorted(handles):
        if key not in _HANDLE_ORDER:
            skipped.append({"key": key, "reason": "no_fetch_strategy"})
    return planned, skipped


def bootstrap_public_presence_fn(
    graph,
    handles: dict[str, Any],
    *,
    source_surface_id: str = DEFAULT_SURFACE_ID,
    budget: int = 10,
    is_fixture: bool = False,
    requested_by: str = "public_presence.bootstrap",
    fetch_provider: str = "public_presence",
    fetch_capability: str = "fetch_page",
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Propose one budgeted bootstrap run over the owner's shared handles.

    Hard budget: at most ``budget`` fetch calls are proposed; overflow is
    logged in the run record with reason ``budget_exhausted``. Execution
    happens when the host pumps the runtime (policy → approval → result);
    the acquisition behavior then lands results as evidence.
    """
    if budget < 1:
        raise ValueError("budget must be >= 1")
    planned, skipped = plan_presence_urls(handles or {})

    in_budget = planned[:budget]
    for kind, url in planned[budget:]:
        skipped.append({"key": kind, "reason": "budget_exhausted", "url": url})

    run = graph.add_object(
        "presence_bootstrap_run",
        {
            "run_identity": _stable_id(
                "presence_run",
                source_surface_id,
                ",".join(url for _kind, url in planned),
                budget,
            ),
            "source_surface_id": source_surface_id,
            "handles": dict(handles or {}),
            "planned_urls": [url for _kind, url in planned],
            "budget": budget,
            "call_ids": [],
            "skipped": skipped,
            "status": "proposed",
            "is_fixture": bool(is_fixture),
            "requested_by": requested_by,
            "metadata": {},
        },
    )

    call_ids: list[str] = []
    for kind, url in in_budget:
        call = graph.add_object(
            "capability_call",
            {
                "provider_id": "",
                "provider_name": fetch_provider,
                "capability_name": fetch_capability,
                "input_data": {"url": url, "timeout_seconds": timeout_seconds},
                "credential_ref_name": None,
                "credential_ref_id": None,
                "risk_class": "low",
                "action_class": "R0",
                "status": "proposed",
                "proposed_by": requested_by,
                "frame_id": None,
                "proposed_at": None,
                "metadata": {
                    "public_presence": {
                        "run_id": run.id,
                        "source_surface_id": source_surface_id,
                        "handle_kind": kind,
                        "url": url,
                        "is_fixture": bool(is_fixture),
                    }
                },
            },
        )
        graph.add_relation(run.id, call.id, "bootstrap_call")
        call_ids.append(call.id)

    graph.patch_object(
        run.id,
        {"call_ids": call_ids, "status": "completed"},
        rationale="bootstrap fetch calls proposed within budget",
    )
    return {
        "ok": True,
        "run_id": run.id,
        "proposed_calls": len(call_ids),
        "planned": len(planned),
        "skipped": skipped,
        "budget": budget,
        "call_ids": call_ids,
    }


@tool(
    name="bootstrap_public_presence",
    description=(
        "Propose a budgeted set of R0 gateway fetches over the owner's "
        "shared public handles (GitHub, X, personal site). Every fetch is "
        "recorded; overflow beyond the budget is logged, never fetched."
    ),
)
def bootstrap_public_presence(
    graph,
    handles: Optional[dict[str, Any]] = None,
    source_surface_id: str = DEFAULT_SURFACE_ID,
    budget: int = 10,
    is_fixture: bool = False,
) -> dict[str, Any]:
    return bootstrap_public_presence_fn(
        graph,
        handles or {},
        source_surface_id=source_surface_id,
        budget=budget,
        is_fixture=is_fixture,
    )


TOOLS = [bootstrap_public_presence]

__all__ = [
    "IMPORTER_ID",
    "IMPORTER_VERSION",
    "DEFAULT_SURFACE_ID",
    "plan_presence_urls",
    "bootstrap_public_presence_fn",
    "bootstrap_public_presence",
    "TOOLS",
]
