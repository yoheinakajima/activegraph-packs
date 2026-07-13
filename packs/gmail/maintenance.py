"""Gmail implementation of the neutral connector maintenance request."""

from __future__ import annotations

from typing import Any

from packs.connector_control.maintenance import register_connector_maintenance_handler

from .tools import request_gmail_poll_fn


def request_gmail_maintenance_fn(graph, binding, request) -> dict[str, Any]:
    surface_id = str(binding.data.get("source_surface_id") or "")
    prior_runs = [
        obj for obj in graph.objects(type="gmail_sync_run")
        if obj.data.get("source_surface_id") == surface_id
    ]
    if not prior_runs:
        raise ValueError("Gmail requires one completed backfill before history polling")
    prior = prior_runs[-1]
    cursors = [
        obj for obj in graph.objects(type="backfill_cursor")
        if obj.data.get("source_surface_id") == surface_id
    ]
    watermark_ref = str(cursors[-1].data.get("watermark_ref") or "") if cursors else ""
    if not watermark_ref.startswith("history:"):
        raise ValueError("Gmail history watermark is unavailable; reconnect/re-explore")
    return request_gmail_poll_fn(
        graph,
        source_surface_id=surface_id,
        account_ref=str(binding.data.get("account_ref") or prior.data.get("account_ref") or ""),
        user_id=str(prior.data.get("user_id") or ""),
        connected_account_id=str(prior.data.get("connected_account_id") or ""),
        start_history_id=watermark_ref.split(":", 1)[1],
        max_messages=100,
        route=str(binding.data.get("active_route") or "composio"),
    )


def register_gmail_maintenance() -> None:
    register_connector_maintenance_handler("gmail", request_gmail_maintenance_fn)


__all__ = ["request_gmail_maintenance_fn", "register_gmail_maintenance"]
