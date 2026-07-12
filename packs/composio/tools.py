"""Graph-visible proposals for Composio route operations."""

from __future__ import annotations

from typing import Any

from activegraph.packs import tool


def _propose(graph, capability: str, action_class: str, risk_class: str, input_data: dict[str, Any], proposed_by: str):
    return graph.add_object(
        "capability_call",
        {
            "provider_id": "",
            "provider_name": "composio",
            "capability_name": capability,
            "input_data": input_data,
            "credential_ref_name": None,
            "credential_ref_id": None,
            "risk_class": risk_class,
            "action_class": action_class,
            "status": "proposed",
            "proposed_by": proposed_by,
            "frame_id": None,
            "proposed_at": None,
            "metadata": {"route": "composio"},
        },
    )


def request_composio_link_fn(graph, *, user_id: str, service: str, callback_url: str):
    return _propose(
        graph, "connections.link", "R4", "high",
        {"user_id": user_id, "service": service, "callback_url": callback_url},
        "composio.connection_ladder",
    )


def request_composio_status_fn(graph, *, user_id: str, service: str, limit: int = 25):
    return _propose(
        graph, "connections.status", "R0", "low",
        {"user_id": user_id, "service": service, "limit": limit},
        "composio.connection_ladder",
    )


@tool(name="request_composio_link", description="Propose an owner-approved Connect Link for one service.")
def request_composio_link(graph, user_id: str = "", service: str = "", callback_url: str = ""):
    return request_composio_link_fn(graph, user_id=user_id, service=service, callback_url=callback_url)


@tool(name="request_composio_status", description="Propose a read-only status check for one service.")
def request_composio_status(graph, user_id: str = "", service: str = "", limit: int = 25):
    return request_composio_status_fn(graph, user_id=user_id, service=service, limit=limit)


TOOLS = [request_composio_link, request_composio_status]

__all__ = ["request_composio_link_fn", "request_composio_status_fn", "TOOLS"]
