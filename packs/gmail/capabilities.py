"""Canonical Gmail capabilities served through the Composio route.

Capability identity is always ``gmail.<operation>``. Composio tool slugs,
account ids, and toolkit versions are route metadata. Provider responses are
stored as replay artifacts; graph capability results contain only hashes,
shape fingerprints, and safe status fields—not mailbox bodies.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from packs.activity_normalizer.replay import portable_artifact_locator, store_replay_artifact
from packs.tool_gateway.integrations import shape_fingerprint


class _AccountInput(BaseModel):
    user_id: str = Field(min_length=1)
    connected_account_id: str = Field(min_length=1)


class GmailProfileInput(_AccountInput):
    pass


class GmailLabelsInput(_AccountInput):
    pass


class GmailFetchInput(_AccountInput):
    query: str = ""
    page_token: str = ""
    max_results: int = Field(default=25, ge=1, le=100)
    include_payload: bool = True


class GmailMessageInput(_AccountInput):
    message_id: str = Field(min_length=1)


class GmailHistoryInput(_AccountInput):
    start_history_id: str = Field(min_length=1)
    page_token: str = ""
    max_results: int = Field(default=100, ge=1, le=500)


class GmailCreateDraftInput(_AccountInput):
    recipient_email: str = Field(min_length=1)
    subject: str = ""
    body: str = ""
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    thread_id: str = ""
    is_html: bool = False
    idempotency_key: str = Field(min_length=1)


class GmailSendDraftInput(_AccountInput):
    draft_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)


_INTERNAL_FIELDS = {"user_id", "connected_account_id", "idempotency_key"}

_COMPOSIO_OPERATIONS = {
    "profile.get": "GMAIL_GET_PROFILE",
    "labels.list": "GMAIL_LIST_LABELS",
    "messages.fetch": "GMAIL_FETCH_EMAILS",
    "messages.get": "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID",
    "history.list": "GMAIL_LIST_HISTORY",
    "drafts.create": "GMAIL_CREATE_EMAIL_DRAFT",
    "drafts.send": "GMAIL_SEND_DRAFT",
}

RouteExecute = Callable[[str, dict[str, Any], str, str, str], Any]


def _default_composio_execute(
    provider_operation: str,
    arguments: dict[str, Any],
    user_id: str,
    connected_account_id: str,
    schema_version: str,
) -> Any:
    # Route code is resolved only when the default adapter actually executes;
    # loading the canonical Gmail pack does not require Composio.
    from packs.composio.client import composio_transport

    return composio_transport().execute(
        tool_slug=provider_operation,
        arguments=arguments,
        user_id=user_id,
        connected_account_id=connected_account_id,
        version=schema_version,
    )


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _execute_to_artifact(
    *,
    provider_operation: str,
    payload: dict[str, Any],
    artifact_store_dir: str,
    route: str,
    schema_version: str,
    execute_route: RouteExecute,
) -> dict[str, Any]:
    user_id = str(payload["user_id"])
    account_id = str(payload["connected_account_id"])
    remote = {
        key: value
        for key, value in payload.items()
        if key not in _INTERNAL_FIELDS and value not in ("", None, [], {})
    }
    result = _plain(
        execute_route(provider_operation, remote, user_id, account_id, schema_version)
    )
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    _artifact_ref, digest = store_replay_artifact(encoded, artifact_store_dir)
    successful = bool(result.get("successful", result.get("success", True))) if isinstance(result, dict) else True
    error = result.get("error") if isinstance(result, dict) else None
    return {
        "ok": successful and not error,
        "successful": successful,
        "error": str(error)[:500] if error else None,
        "route": route,
        "provider_operation": provider_operation,
        "route_schema_version": schema_version,
        "connected_account_id": account_id,
        "replay_mode": "artifact",
        "replay_artifact_locator": portable_artifact_locator(digest),
        "shape_fingerprint": shape_fingerprint(result),
    }


def register_gmail_capabilities(
    *,
    artifact_store_dir: str = ".activegraph/replay-artifacts",
    toolkit_version: str = "20260703_00",
    route: str = "composio",
    execute_route: Optional[RouteExecute] = None,
    operation_map: Optional[dict[str, str]] = None,
) -> tuple:
    from packs.tool_gateway.tools import register_local_capability

    executor = execute_route or _default_composio_execute
    operations = dict(operation_map or _COMPOSIO_OPERATIONS)

    def handler(operation: str):
        provider_operation = operations.get(operation)
        if not provider_operation:
            raise ValueError(f"route {route!r} does not map Gmail operation {operation!r}")

        def _run(execution_context: Optional[dict] = None, **kwargs):
            return _execute_to_artifact(
                provider_operation=provider_operation,
                payload=kwargs,
                artifact_store_dir=artifact_store_dir,
                route=route,
                schema_version=toolkit_version,
                execute_route=executor,
            )
        return _run

    return (
        register_local_capability(
            "gmail", "profile.get", handler("profile.get"),
            input_schema=GmailProfileInput,
            description="Read Gmail account identity, totals, and history watermark.",
            risk_class="low", action_class="R0",
        ),
        register_local_capability(
            "gmail", "labels.list", handler("labels.list"),
            input_schema=GmailLabelsInput,
            description="List Gmail label topology without reading message content.",
            risk_class="low", action_class="R0",
        ),
        register_local_capability(
            "gmail", "messages.fetch", handler("messages.fetch"),
            input_schema=GmailFetchInput,
            description="Fetch one bounded Gmail message page for ingestion.",
            risk_class="low", action_class="R0",
        ),
        register_local_capability(
            "gmail", "messages.get", handler("messages.get"),
            input_schema=GmailMessageInput,
            description="Fetch one Gmail message by provider-stable id.",
            risk_class="low", action_class="R0",
        ),
        register_local_capability(
            "gmail", "history.list", handler("history.list"),
            input_schema=GmailHistoryInput,
            description="Read Gmail mailbox changes from a provider history watermark.",
            risk_class="low", action_class="R0",
        ),
        register_local_capability(
            "gmail", "drafts.create", handler("drafts.create"),
            input_schema=GmailCreateDraftInput,
            description="Create a reversible Gmail-hosted draft from a local candidate.",
            risk_class="medium", action_class="R2",
        ),
        register_local_capability(
            "gmail", "drafts.send", handler("drafts.send"),
            input_schema=GmailSendDraftInput,
            description="Send an existing Gmail draft; outward R3 forever.",
            risk_class="high", action_class="R3",
        ),
    )


__all__ = [
    "GmailProfileInput", "GmailLabelsInput", "GmailFetchInput", "GmailMessageInput",
    "GmailHistoryInput", "GmailCreateDraftInput", "GmailSendDraftInput",
    "RouteExecute", "register_gmail_capabilities",
]
