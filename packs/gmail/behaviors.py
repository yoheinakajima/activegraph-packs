"""Turn recorded Gmail route results into profiles, evidence, and cursors."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, Optional

from activegraph.packs import behavior

from packs.activity_normalizer.behaviors import record_failure
from packs.activity_normalizer.replay import (
    artifact_identity_from_locator,
    read_artifact,
    store_replay_artifact,
)
from packs.tool_gateway.integrations import (
    ensure_aggregator_profile_fn,
    record_integration_profile_fn,
    shape_fingerprint,
    stable_integration_id,
)
from packs.tool_gateway.untrusted import scan_for_injection

from .control_plane import (
    adapt_gmail_run_fn,
    gmail_learning_settled,
    gmail_run_id_for_object,
)

from .settings import GmailSettings
from .tools import _capability_call, propose_gmail_page_fn


_VIEW = {
    "include_types": [
        "capability_call", "capability_result", "integration_exploration",
        "integration_profile", "aggregator_profile", "gmail_sync_run",
        "backfill_cursor", "source_connection_request", "gmail_draft_candidate",
        "evidence_invalidation_request", "ingestion_failure",
    ],
    "recent_events": 20_000,
}

_CONTROL_VIEW = {
    "include_types": [
        "gmail_sync_run", "backfill_cursor",
        "connector_surface_binding", "connector_run_observation",
        "connector_learning_delta", "connector_native_view",
        "activity_evidence", "semantic_annotation", "extraction_coverage",
        "ingestion_failure",
        "preference_candidate", "task_candidate", "profile_candidate",
        "skill_candidate", "eval_candidate",
    ],
    "recent_events": 20_000,
}


def _json(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gmail capability result is not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Gmail capability result must be a JSON object")
    return parsed


def _read_route_payload(envelope: dict[str, Any], settings: GmailSettings) -> Any:
    if envelope.get("replay_mode") != "artifact":
        raise ValueError("Gmail route result is not replay-complete artifact data")
    artifact_ref, digest = artifact_identity_from_locator(
        str(envelope.get("replay_artifact_locator") or "")
    )
    raw = read_artifact(
        artifact_ref,
        digest,
        settings.artifact_store_dir,
        max_bytes=settings.max_replay_payload_bytes,
    )
    return json.loads(raw.decode("utf-8"))


def _unwrap(value: Any) -> Any:
    current = value
    for _ in range(5):
        if not isinstance(current, dict):
            break
        candidate = None
        for key in ("response_data", "responseData", "data"):
            nested = current.get(key)
            if isinstance(nested, (dict, list)):
                candidate = nested
                break
        if candidate is None:
            break
        current = candidate
    return current


def _find_key(value: Any, names: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        for name in names:
            if name in value and value[name] not in (None, ""):
                return value[name]
        for child in value.values():
            found = _find_key(child, names)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_key(child, names)
            if found not in (None, ""):
                return found
    return None


def _find_list(value: Any, names: tuple[str, ...]) -> list[Any]:
    if isinstance(value, dict):
        for name in names:
            candidate = value.get(name)
            if isinstance(candidate, list):
                return candidate
        for child in value.values():
            found = _find_list(child, names)
            if found:
                return found
    return []


def _call_for(graph, result_data: dict[str, Any]):
    call_id = str(result_data.get("call_id") or "")
    call = graph.get_object(call_id) if call_id else None
    return call if call is not None and call.type == "capability_call" else None


def _exploration(ctx, key: str):
    return next(
        (
            obj for obj in ctx.view.objects(type="integration_exploration")
            if ((obj.data or {}).get("metadata") or {}).get("exploration_key") == key
            and (obj.data or {}).get("status") != "completed"
        ),
        None,
    )


def _labels(payload: Any) -> list[dict[str, Any]]:
    rows = _find_list(payload, ("labels", "items"))
    result = []
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        result.append({
            "id": str(row.get("id") or ""),
            "name": str(row.get("name") or row.get("label") or "")[:200],
            "type": str(row.get("type") or "unknown"),
        })
    return result


def _inventory(version: str, route: str) -> list[dict[str, Any]]:
    rows = [
        ("gmail.profile.get", "R0", "GMAIL_GET_PROFILE", "none"),
        ("gmail.labels.list", "R0", "GMAIL_LIST_LABELS", "none"),
        ("gmail.messages.fetch", "R0", "GMAIL_FETCH_EMAILS", "none"),
        ("gmail.messages.get", "R0", "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID", "none"),
        ("gmail.history.list", "R0", "GMAIL_LIST_HISTORY", "none"),
        ("gmail.drafts.create", "R2", "GMAIL_CREATE_EMAIL_DRAFT", "client_guard"),
        ("gmail.drafts.send", "R3", "GMAIL_SEND_DRAFT", "natural"),
    ]
    return [
        {
            "operation": operation,
            "action_class": action,
            "classification_source": "default",
            "route": route,
            "provider_operation": provider_operation if route == "composio" else None,
            "input_schema_fingerprint": None,
            "idempotency": idempotency,
            "metadata": {"route_schema_version": version},
        }
        for operation, action, provider_operation, idempotency in rows
    ]


@behavior(
    name="gmail_exploration_projector",
    on=["object.created"],
    where={"object.type": "capability_result"},
    view=_VIEW,
    creates=["integration_profile", "aggregator_profile", "source_connection_request", "backfill_cursor"],
)
def gmail_exploration_projector(event, graph, ctx, *, settings: GmailSettings):
    result_data = ((event.payload or {}).get("object") or {}).get("data") or {}
    if result_data.get("provider_name") != "gmail":
        return
    call = _call_for(graph, result_data)
    if call is None:
        return
    gmail_meta = ((call.data or {}).get("metadata") or {}).get("gmail") or {}
    if gmail_meta.get("kind") != "exploration":
        return
    key = str(gmail_meta.get("exploration_key") or "")
    receipt = _exploration(ctx, key)
    if receipt is None:
        return
    envelope = _json(str(result_data.get("output_data") or ""))
    metadata = dict((receipt.data or {}).get("metadata") or {})
    results = dict(metadata.get("results") or {})
    probe = str(gmail_meta.get("probe") or "")
    results[probe] = envelope
    metadata["results"] = results
    if not envelope.get("ok"):
        graph.patch_object(
            receipt.id,
            {"status": "failed", "metadata": metadata},
        )
        return
    if not {"profile", "labels"} <= set(results):
        graph.patch_object(receipt.id, {"status": "partial", "metadata": metadata})
        return

    try:
        profile_payload = _read_route_payload(results["profile"], settings)
        labels_payload = _read_route_payload(results["labels"], settings)
    except (OSError, RuntimeError, ValueError) as exc:
        metadata["error"] = f"{type(exc).__name__}: {exc}"[:500]
        graph.patch_object(receipt.id, {"status": "failed", "metadata": metadata})
        return
    email = str(_find_key(profile_payload, ("emailAddress", "email_address", "email")) or "").strip().lower()
    if not email:
        graph.patch_object(receipt.id, {"status": "failed", "metadata": metadata})
        return
    account_id = str(gmail_meta.get("connected_account_id") or "")
    route = str(gmail_meta.get("route") or (receipt.data.get("metadata") or {}).get("route") or "composio")
    route_schema_version = str(
        results["profile"].get("route_schema_version")
        or settings.toolkit_version
    )
    history_id = str(_find_key(profile_payload, ("historyId", "history_id")) or "") or None
    label_rows = _labels(labels_payload)
    observed = {"profile": profile_payload, "labels": labels_payload}
    aggregator = None
    if route == "composio":
        aggregator = ensure_aggregator_profile_fn(
            graph,
            aggregator="composio",
            user_ref=str((call.data or {}).get("input_data", {}).get("user_id") or ""),
            auth_state="active",
            available_services=["gmail"],
            enabled_services=["gmail"],
            metadata={"catalog_enumerated": False},
            reader=ctx.view,
        )
    profile, _ = record_integration_profile_fn(
        graph,
        service="gmail",
        account_ref=email,
        account_display=email,
        routes=[{
            "path": route, "route_ref": f"{route}:{account_id}",
            "status": "active", "connected_account_id": account_id,
            "schema_version": route_schema_version,
            "metadata": (
                {"aggregator_profile_id": aggregator.id}
                if aggregator is not None else {}
            ),
        }],
        facets=["record_store", "effector", "social_graph"],
        capability_inventory=_inventory(route_schema_version, route),
        data_topology={
            "containers": label_rows,
            "item_types": ["message", "thread", "draft"],
            "volume_estimate": {
                "messages": _find_key(profile_payload, ("messagesTotal", "messages_total")),
                "threads": _find_key(profile_payload, ("threadsTotal", "threads_total")),
            },
            "history_watermark": history_id,
        },
        signal_map=[
            {
                "surface": "inbox",
                "candidate_types": ["relationship", "task", "preference", "followup", "project"],
                "estimated_richness": "high",
                "confidence": 0.6,
                "provenance": [receipt.id],
            }
        ],
        claims=[
            {
                "claim_key": "account.identity",
                "value": email,
                "confidence": 1.0,
                "freshness": "current",
                "provenance": list(receipt.data.get("probe_call_ids") or []),
                "classification_source": "evidence",
                "asserted_by": "gmail.integration_explorer",
                "observed_at_event_id": event.id,
                "metadata": {"structural_only": True},
            },
            {
                "claim_key": "mailbox.label_topology",
                "value": label_rows,
                "confidence": 0.95,
                "freshness": "current",
                "provenance": list(receipt.data.get("probe_call_ids") or []),
                "classification_source": "evidence",
                "asserted_by": "gmail.integration_explorer",
                "observed_at_event_id": event.id,
                "metadata": {"structural_only": True},
            },
            {
                "claim_key": "signal.inbox_richness",
                "value": "high",
                "confidence": 0.6,
                "freshness": "current",
                "provenance": [receipt.id],
                "classification_source": "evidence",
                "asserted_by": "gmail.integration_explorer",
                "observed_at_event_id": event.id,
                "metadata": {"predicted": True},
            },
        ],
        health={
            "drift_state": "current",
            "shape_fingerprint": shape_fingerprint(observed),
            "last_explored_event_id": event.id,
            "route_schema_version": route_schema_version,
        },
        exploration_receipts=[receipt.id],
        metadata={"scope_visibility": "unknown", "discovered_metadata_trusted": False},
        reader=ctx.view,
    )
    surface_id = stable_integration_id("surface", "gmail", email, route)
    request_identity = stable_integration_id("source_connection", surface_id, route)
    prior_request = next(
        (
            obj for obj in ctx.view.objects(type="source_connection_request")
            if (obj.data or {}).get("request_identity") == request_identity
        ),
        None,
    )
    if prior_request is None:
        graph.add_object(
            "source_connection_request",
            {
                "request_identity": request_identity,
                "surface_id": surface_id,
                "category": "communication",
                "provider": {
                    "service": "gmail", "account_ref": email,
                    "connected_account_id": account_id, "route": route,
                },
                "path": route,
                "privacy_scope": "account",
                "adapter": "gmail@0.1.0",
                "acquisition_mode": "backfill",
                "status": "proposed",
                "surface_object_id": None,
                "error": None,
                "metadata": {"integration_profile_id": profile.id},
            },
        )
    if history_id and not any(
        (obj.data or {}).get("source_surface_id") == surface_id
        for obj in ctx.view.objects(type="backfill_cursor")
    ):
        graph.add_object(
            "backfill_cursor",
            {
                "source_surface_id": surface_id,
                "oldest_ingested_ref": None,
                "newest_ingested_ref": None,
                "watermark_ref": f"history:{history_id}",
                "cursor_version": 1,
            },
        )
    graph.patch_object(
        receipt.id,
        {
            "account_ref": email,
            "profile_id": profile.id,
            "shape_fingerprint": shape_fingerprint(observed),
            "status": "completed",
            "metadata": metadata,
        },
    )
    graph.add_relation(profile.id, receipt.id, "profile_explored_by")


def _header_map(message: dict[str, Any]) -> dict[str, str]:
    payload = message.get("payload") or {}
    rows = payload.get("headers") if isinstance(payload, dict) else None
    headers: dict[str, str] = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("name"):
                headers[str(row["name"]).lower()] = str(row.get("value") or "")
    for key in ("from", "to", "cc", "bcc", "subject", "date", "message-id"):
        direct = message.get(key) or message.get(key.replace("-", "_"))
        if direct and key not in headers:
            headers[key] = str(direct)
    return headers


def _decode_body_data(value: str) -> str:
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(padded.encode()).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _body(message: dict[str, Any]) -> str:
    for key in ("messageText", "message_text", "body_text", "text", "body", "snippet"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value
    payload = message.get("payload") or {}
    if isinstance(payload, dict):
        body = payload.get("body") or {}
        if isinstance(body, dict) and isinstance(body.get("data"), str):
            decoded = _decode_body_data(body["data"])
            if decoded:
                return decoded
        for part in payload.get("parts") or []:
            if isinstance(part, dict):
                candidate = _body({"payload": part})
                if candidate:
                    return candidate
    return ""


def _message_rows(payload: Any) -> list[dict[str, Any]]:
    rows = _find_list(payload, ("messages", "emails", "items"))
    return [row for row in rows if isinstance(row, dict)]


def _has_named_list(value: Any, names: tuple[str, ...]) -> bool:
    if isinstance(value, list):
        return True
    if not isinstance(value, dict):
        return False
    for name in names:
        if name in value and isinstance(value[name], list):
            return True
    return any(_has_named_list(child, names) for child in value.values())


def _message_id(message: dict[str, Any]) -> str:
    return str(message.get("id") or message.get("message_id") or message.get("messageId") or "")


def _normalized_message(message: dict[str, Any], max_chars: int) -> tuple[str, dict[str, Any]]:
    headers = _header_map(message)
    body = _body(message)
    lines = [
        f"Subject: {headers.get('subject', '')}",
        f"From: {headers.get('from', '')}",
        f"To: {headers.get('to', '')}",
        f"Date: {headers.get('date', '')}",
        "",
        body,
    ]
    content = "\n".join(lines)[:max_chars]
    return content, {
        "service": "gmail",
        "message_id": _message_id(message),
        "thread_id": str(message.get("threadId") or message.get("thread_id") or ""),
        "history_id": str(message.get("historyId") or message.get("history_id") or ""),
        "message_id_header": headers.get("message-id"),
        "from": headers.get("from"),
        "to": headers.get("to"),
        "cc": headers.get("cc"),
        "subject": headers.get("subject"),
        "date": headers.get("date") or None,
        "labels": message.get("labelIds") or message.get("label_ids") or [],
        "injection_flags": scan_for_injection(content),
        "shape_fingerprint": shape_fingerprint(message),
        "truncated": len("\n".join(lines)) > max_chars,
    }


def _import_messages(graph, messages: list[dict[str, Any]], run, settings: GmailSettings) -> list[str]:
    imported: list[str] = []
    for message in messages:
        message_id = _message_id(message)
        if not message_id:
            continue
        canonical = json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        replay_ref, digest = store_replay_artifact(canonical, settings.artifact_store_dir)
        normalized, metadata = _normalized_message(message, settings.max_normalized_chars)
        acquired = graph.add_object(
            "acquired_item",
            {
                "source_surface_id": run.data["source_surface_id"],
                "provider_item_id": message_id,
                "dedup_key": message_id,
                "source_ref": f"gmail://{run.data['connected_account_id']}/messages/{message_id}",
                "source_hash": digest,
                "provider_time": metadata.get("date") or None,
                "replay_mode": "artifact",
                "replay_payload_ref": replay_ref,
                "replay_payload_hash": digest,
                "media_type": "message/rfc822+json",
                "importer_id": "gmail",
                "importer_version": "0.1.0",
            },
        )
        graph.add_object(
            "acquired_content",
            {
                "acquired_item_id": acquired.id,
                "normalized_content": normalized,
                "normalized_metadata": {
                    **metadata,
                    "account_ref": run.data["account_ref"],
                    "route": str((run.data.get("metadata") or {}).get("route") or "composio"),
                    "connected_account_id": run.data["connected_account_id"],
                    "connector_run_id": run.id,
                },
                "source_category": "communication",
                "connection_path": str((run.data.get("metadata") or {}).get("route") or "composio"),
                "is_fixture": bool((run.data.get("metadata") or {}).get("is_fixture", False)),
            },
        )
        imported.append(message_id)
    return imported


def _cursor(ctx, surface_id: str):
    return next((obj for obj in ctx.view.objects(type="backfill_cursor") if (obj.data or {}).get("source_surface_id") == surface_id), None)


def _advance_cursor(graph, cursor, *, surface_id: str, message_ids: list[str], watermark: Optional[str] = None):
    updates: dict[str, Any] = {}
    if message_ids:
        updates["newest_ingested_ref"] = (cursor.data.get("newest_ingested_ref") if cursor else None) or f"message:{message_ids[0]}"
        updates["oldest_ingested_ref"] = f"message:{message_ids[-1]}"
    if watermark:
        updates["watermark_ref"] = f"history:{watermark}"
    if cursor is None:
        return graph.add_object(
            "backfill_cursor",
            {
                "source_surface_id": surface_id,
                "oldest_ingested_ref": updates.get("oldest_ingested_ref"),
                "newest_ingested_ref": updates.get("newest_ingested_ref"),
                "watermark_ref": updates.get("watermark_ref"),
                "cursor_version": 1,
            },
        )
    if updates:
        graph.patch_object(cursor.id, updates)
    return graph.get_object(cursor.id)


def _next_page(payload: Any) -> Optional[str]:
    value = _find_key(payload, ("nextPageToken", "next_page_token", "page_token"))
    return str(value) if value else None


def _history_changes(payload: Any) -> tuple[list[str], list[str], Optional[str]]:
    history = _find_list(payload, ("history", "items"))
    ids: list[str] = []
    deleted: list[str] = []
    for row in history:
        if not isinstance(row, dict):
            continue
        for bucket in (
            "messagesAdded", "messages_added", "messages",
            "labelsAdded", "labels_added", "labelsRemoved", "labels_removed",
        ):
            values = row.get(bucket) or []
            if isinstance(values, dict):
                values = [values]
            for item in values:
                if isinstance(item, dict) and isinstance(item.get("message"), dict):
                    item = item["message"]
                if isinstance(item, dict):
                    mid = _message_id(item)
                    if mid and mid not in ids:
                        ids.append(mid)
        for bucket in ("messagesDeleted", "messages_deleted", "deleted"):
            values = row.get(bucket) or []
            if isinstance(values, dict):
                values = [values]
            for item in values:
                if isinstance(item, dict) and isinstance(item.get("message"), dict):
                    item = item["message"]
                if isinstance(item, dict):
                    mid = _message_id(item)
                    if mid and mid not in deleted:
                        deleted.append(mid)
    watermark = _find_key(payload, ("historyId", "history_id"))
    return ids, deleted, str(watermark) if watermark else None


def _classify_provider_failure(message: str) -> str:
    lowered = message.lower()
    if "429" in lowered or "rate limit" in lowered or "rate_limit" in lowered:
        return "rate_limited"
    if "401" in lowered or "403" in lowered or "auth" in lowered or "credential" in lowered:
        return "auth_expired"
    if "history" in lowered and any(word in lowered for word in ("invalid", "expired", "404", "too old")):
        return "cursor_invalid"
    return "provider_failed"


def _active_profile(ctx, account_ref: str):
    return next(
        (
            obj for obj in ctx.view.objects(type="integration_profile")
            if obj.data.get("service") == "gmail"
            and obj.data.get("account_ref") == account_ref
            and obj.data.get("status") == "active"
        ),
        None,
    )


def _mark_profile_drift(graph, ctx, run, *, reason: str, call_id: str, observed_shape: Any = None):
    profile = _active_profile(ctx, str(run.data.get("account_ref") or ""))
    if profile is None:
        return None
    data = profile.data or {}
    health = {
        **dict(data.get("health") or {}),
        "drift_state": "stale",
        "drift_reason": reason,
        "drift_call_id": call_id,
    }
    if observed_shape is not None:
        health["unexpected_shape_fingerprint"] = shape_fingerprint(observed_shape)
    claims = [
        {**dict(claim), "freshness": "stale"}
        for claim in data.get("claims") or []
    ]
    stale, _ = record_integration_profile_fn(
        graph,
        service=data["service"],
        account_ref=data["account_ref"],
        account_display=data.get("account_display"),
        routes=list(data.get("routes") or []),
        scopes_granted=list(data.get("scopes_granted") or []),
        scopes_available=list(data.get("scopes_available") or []),
        facets=list(data.get("facets") or []),
        capability_inventory=list(data.get("capability_inventory") or []),
        data_topology=dict(data.get("data_topology") or {}),
        signal_map=list(data.get("signal_map") or []),
        claims=claims,
        health=health,
        exploration_receipts=list(data.get("exploration_receipts") or []),
        metadata=dict(data.get("metadata") or {}),
        profile_status="stale",
        reader=ctx.view,
    )
    return stale


def _fail_sync(graph, ctx, run, *, code: str, message: str, call_id: str, payload: Any = None):
    recoverable = code in {"rate_limited", "cursor_invalid", "unexpected_shape"}
    record_failure(
        graph,
        stage="acquisition",
        error_code=f"gmail.{code}",
        message=message,
        source_surface_id=run.data.get("source_surface_id"),
        source_ref=f"gmail://{run.data.get('connected_account_id')}/sync/{run.id}",
        importer_id="gmail",
        importer_version="0.1.0",
        recoverable=recoverable,
        metadata={"run_id": run.id, "call_id": call_id},
    )
    metadata = dict(run.data.get("metadata") or {})
    if code == "cursor_invalid":
        metadata["reanchor_required"] = True
    graph.patch_object(
        run.id,
        {"status": "failed", "error_code": code, "error": message[:500], "metadata": metadata},
    )
    if code in {"auth_expired", "cursor_invalid", "unexpected_shape"}:
        _mark_profile_drift(
            graph, ctx, run, reason=code, call_id=call_id, observed_shape=payload
        )


@behavior(
    name="gmail_sync_result_ingester",
    on=["object.created"],
    where={"object.type": "capability_result"},
    view=_VIEW,
    creates=[
        "acquired_item", "acquired_content", "backfill_cursor", "capability_call",
        "evidence_invalidation_request", "ingestion_failure", "integration_profile",
    ],
)
def gmail_sync_result_ingester(event, graph, ctx, *, settings: GmailSettings):
    result_data = ((event.payload or {}).get("object") or {}).get("data") or {}
    if result_data.get("provider_name") != "gmail":
        return
    call = _call_for(graph, result_data)
    if call is None:
        return
    meta = ((call.data or {}).get("metadata") or {}).get("gmail") or {}
    kind = meta.get("kind")
    if kind not in {"backfill", "history", "history_message"}:
        return
    run = graph.get_object(str(meta.get("run_id") or ""))
    if run is None or run.type != "gmail_sync_run" or run.data.get("status") not in {"running", "partial"}:
        return
    envelope = _json(str(result_data.get("output_data") or ""))
    if not envelope.get("ok"):
        message = str(envelope.get("error") or "Gmail call failed")
        _fail_sync(
            graph,
            ctx,
            run,
            code=_classify_provider_failure(message),
            message=message,
            call_id=call.id,
        )
        return
    try:
        payload = _read_route_payload(envelope, settings)
    except (OSError, RuntimeError, ValueError) as exc:
        _fail_sync(
            graph,
            ctx,
            run,
            code="unexpected_shape",
            message=f"Gmail replay artifact could not be read: {type(exc).__name__}: {exc}",
            call_id=call.id,
        )
        return

    if kind == "backfill":
        if not _has_named_list(payload, ("messages", "emails", "items")):
            _fail_sync(
                graph, ctx, run, code="unexpected_shape",
                message="Gmail message page omitted a recognized message collection",
                call_id=call.id, payload=payload,
            )
            return
        remaining = max(0, int(run.data["max_messages"]) - int(run.data.get("messages_imported", 0)))
        rows = _message_rows(payload)[:remaining]
        imported = _import_messages(graph, rows, run, settings)
        cursor = _advance_cursor(graph, _cursor(ctx, run.data["source_surface_id"]), surface_id=run.data["source_surface_id"], message_ids=imported)
        pages = int(run.data.get("pages_completed", 0)) + 1
        count = int(run.data.get("messages_imported", 0)) + len(imported)
        token = _next_page(payload)
        bound_hit = count >= int(run.data["max_messages"]) or pages >= int(run.data["max_pages"])
        status = "partial" if token and bound_hit else ("running" if token else "completed")
        graph.patch_object(run.id, {"pages_completed": pages, "messages_imported": count, "next_page_token": token, "status": status})
        if token and not bound_hit:
            propose_gmail_page_fn(graph, graph.get_object(run.id), page_token=token)
        return

    if kind == "history":
        if not _has_named_list(payload, ("history", "items")):
            _fail_sync(
                graph, ctx, run, code="unexpected_shape",
                message="Gmail history page omitted a recognized history collection",
                call_id=call.id, payload=payload,
            )
            return
        message_ids, deleted_ids, watermark = _history_changes(payload)
        message_ids = message_ids[: int(run.data["max_messages"])]
        tombstones = 0
        for message_id in deleted_ids[: int(run.data["max_messages"])]:
            identity = stable_integration_id(
                "gmail_tombstone", run.data["source_surface_id"], message_id, watermark or ""
            )
            existing = next(
                (
                    obj for obj in ctx.view.objects(type="evidence_invalidation_request")
                    if obj.data.get("request_identity") == identity
                ),
                None,
            )
            if existing is None:
                graph.add_object(
                    "evidence_invalidation_request",
                    {
                        "request_identity": identity,
                        "source_surface_id": run.data["source_surface_id"],
                        "provider_item_id": message_id,
                        "evidence_identity": None,
                        "reason": "provider_deleted",
                        "status": "proposed",
                        "invalidated_evidence_ids": [],
                        "error": None,
                        "metadata": {"history_id": watermark, "gmail_sync_run_id": run.id},
                    },
                )
                tombstones += 1
        calls = list(run.data.get("call_ids") or [])
        for message_id in message_ids:
            fetch = _capability_call(
                graph,
                operation="messages.get", action_class="R0", risk_class="low",
                input_data={
                    "user_id": run.data["user_id"],
                    "connected_account_id": run.data["connected_account_id"],
                    "message_id": message_id,
                },
                metadata={"kind": "history_message", "run_id": run.id, "message_id": message_id},
                proposed_by="gmail.poll",
                route=str((run.data.get("metadata") or {}).get("route") or "composio"),
            )
            graph.add_relation(run.id, fetch.id, "gmail_sync_call")
            calls.append(fetch.id)
        updates = {
            "pending_message_ids": message_ids,
            "latest_history_id": watermark,
            "deleted_message_ids": deleted_ids,
            "tombstones_recorded": int(run.data.get("tombstones_recorded", 0)) + tombstones,
            "call_ids": calls,
            "pages_completed": 1,
        }
        if not message_ids:
            _advance_cursor(graph, _cursor(ctx, run.data["source_surface_id"]), surface_id=run.data["source_surface_id"], message_ids=[], watermark=watermark)
            updates["status"] = "completed"
        graph.patch_object(run.id, updates)
        return

    rows = _message_rows(payload)
    if not rows:
        unwrapped = _unwrap(payload)
        if isinstance(unwrapped, dict) and _message_id(unwrapped):
            rows = [unwrapped]
    if not rows:
        _fail_sync(
            graph, ctx, run, code="unexpected_shape",
            message="Gmail message lookup omitted the requested message",
            call_id=call.id, payload=payload,
        )
        return
    imported = _import_messages(graph, rows[:1], run, settings)
    completed = list(run.data.get("completed_message_ids") or [])
    for message_id in imported:
        if message_id not in completed:
            completed.append(message_id)
    pending = list(run.data.get("pending_message_ids") or [])
    done = set(completed) >= set(pending)
    updates = {"completed_message_ids": completed, "messages_imported": len(completed)}
    if done:
        _advance_cursor(
            graph, _cursor(ctx, run.data["source_surface_id"]),
            surface_id=run.data["source_surface_id"], message_ids=completed,
            watermark=run.data.get("latest_history_id"),
        )
        updates["status"] = "completed"
    graph.patch_object(run.id, updates)


@behavior(
    name="gmail_draft_result_projector",
    on=["object.created"],
    where={"object.type": "capability_result"},
    view=_VIEW,
)
def gmail_draft_result_projector(event, graph, ctx, *, settings: GmailSettings):
    """Commit only approved provider draft effects back to the local draft."""

    result_data = ((event.payload or {}).get("object") or {}).get("data") or {}
    if result_data.get("provider_name") != "gmail":
        return
    call = _call_for(graph, result_data)
    if call is None:
        return
    meta = ((call.data or {}).get("metadata") or {}).get("gmail") or {}
    kind = meta.get("kind")
    if kind not in {"draft_create", "draft_send"}:
        return
    draft = graph.get_object(str(meta.get("draft_id") or ""))
    if draft is None or draft.type != "gmail_draft_candidate":
        return
    envelope = _json(str(result_data.get("output_data") or ""))
    if not envelope.get("ok"):
        metadata = dict(draft.data.get("metadata") or {})
        metadata["last_error"] = str(envelope.get("error") or "Gmail draft effect failed")[:500]
        graph.patch_object(draft.id, {"status": "rejected", "metadata": metadata})
        return
    if kind == "draft_create":
        try:
            payload = _read_route_payload(envelope, settings)
        except (OSError, RuntimeError, ValueError) as exc:
            metadata = dict(draft.data.get("metadata") or {})
            metadata["last_error"] = (
                f"Gmail draft replay artifact could not be read: {type(exc).__name__}: {exc}"
            )[:500]
            graph.patch_object(draft.id, {"status": "rejected", "metadata": metadata})
            return
        provider_id = str(_find_key(_unwrap(payload), ("draftId", "draft_id", "id")) or "")
        if not provider_id:
            metadata = dict(draft.data.get("metadata") or {})
            metadata["last_error"] = "Gmail create-draft response omitted a provider draft id"
            graph.patch_object(draft.id, {"status": "rejected", "metadata": metadata})
            return
        graph.patch_object(
            draft.id,
            {"status": "synced", "provider_draft_id": provider_id},
        )
        return
    graph.patch_object(draft.id, {"status": "sent"})


@behavior(
    name="gmail_control_plane_adapter",
    on=["patch.applied"],
    view=_CONTROL_VIEW,
    creates=[
        "connector_surface_binding", "connector_run_observation",
        "connector_learning_delta", "connector_native_view",
    ],
)
def gmail_control_plane_adapter(event, graph, ctx, *, settings: GmailSettings):
    """Keep the neutral control plane aligned with authoritative Gmail runs."""

    object_id = str((event.payload or {}).get("target") or "")
    obj = graph.get_object(object_id) if object_id else None
    if obj is None or obj.type != "gmail_sync_run":
        return
    status_diff = ((event.payload or {}).get("diff") or {}).get("status") or {}
    adapt_gmail_run_fn(
        graph,
        obj,
        source_event_id=event.id,
        attempt=(
            status_diff.get("new") == "running"
            and status_diff.get("old") == "failed"
        ),
        reader=ctx.view,
    )


_CONTROL_CREATES = [
    "connector_surface_binding", "connector_run_observation",
    "connector_learning_delta", "connector_native_view",
]


@behavior(
    name="gmail_control_plane_run_created",
    on=["object.created"],
    where={"object.type": "gmail_sync_run"},
    view=_CONTROL_VIEW,
    creates=_CONTROL_CREATES,
)
def gmail_control_plane_run_created(event, graph, ctx, *, settings: GmailSettings):
    wrapper = (event.payload or {}).get("object") or {}
    run = graph.get_object(str(wrapper.get("id") or ""))
    if run is None:
        return
    adapt_gmail_run_fn(
        graph, run, source_event_id=event.id, attempt=True, reader=ctx.view
    )


@behavior(
    name="gmail_control_plane_learning_settled",
    on=["object.created"],
    where={"object.type": "extraction_coverage"},
    view=_CONTROL_VIEW,
    creates=_CONTROL_CREATES,
)
def gmail_control_plane_learning_settled(
    event, graph, ctx, *, settings: GmailSettings
):
    wrapper = (event.payload or {}).get("object") or {}
    data = dict(wrapper.get("data") or {})
    run_id = gmail_run_id_for_object(ctx.view, "extraction_coverage", data)
    run = graph.get_object(run_id) if run_id else None
    if run is None or not gmail_learning_settled(ctx.view, run):
        return
    adapt_gmail_run_fn(
        graph, run, source_event_id=None, attempt=False, reader=ctx.view
    )


@behavior(
    name="gmail_control_plane_failure",
    on=["object.created"],
    where={"object.type": "ingestion_failure"},
    view=_CONTROL_VIEW,
    creates=_CONTROL_CREATES,
)
def gmail_control_plane_failure(event, graph, ctx, *, settings: GmailSettings):
    wrapper = (event.payload or {}).get("object") or {}
    data = dict(wrapper.get("data") or {})
    run_id = gmail_run_id_for_object(ctx.view, "ingestion_failure", data)
    run = graph.get_object(run_id) if run_id else None
    if run is None:
        return
    adapt_gmail_run_fn(
        graph, run, source_event_id=None, attempt=False, reader=ctx.view
    )


BEHAVIORS = [
    gmail_exploration_projector,
    gmail_sync_result_ingester,
    gmail_draft_result_projector,
    gmail_control_plane_adapter,
    gmail_control_plane_run_created,
    gmail_control_plane_learning_settled,
    gmail_control_plane_failure,
]

__all__ = ["BEHAVIORS"]
