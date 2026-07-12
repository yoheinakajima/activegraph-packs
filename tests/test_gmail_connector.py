"""Gmail is the first full connector-conformance case."""

from __future__ import annotations

import pytest

from activegraph import Graph, Runtime

from packs.activity_normalizer import ActivityNormalizerSettings, pack as normalizer_pack
from packs.composio import pack as composio_pack
from packs.composio.capabilities import register_composio_capabilities
from packs.composio.client import (
    ComposioUnavailable,
    configure_composio_transport,
    resolve_catalog_tool,
    take_redirect,
)
from packs.composio.tools import request_composio_link_fn
from packs.core import pack as core_pack
from packs.gmail import GmailSettings, pack as gmail_pack
from packs.gmail.capabilities import register_gmail_capabilities
from packs.gmail.tools import (
    create_gmail_draft_candidate_fn,
    request_gmail_backfill_fn,
    request_gmail_draft_send_fn,
    request_gmail_draft_sync_fn,
    request_gmail_exploration_fn,
    request_gmail_poll_fn,
)
from packs.semantic_extraction import pack as extraction_pack
from packs.tool_gateway import pack as gateway_pack
from packs.tool_gateway.integrations import correct_integration_claim_fn
from packs.tool_gateway.tools import approve_capability_fn, clear_local_registry
from packs.usage import pack as usage_pack
from packs.usage.tools import set_surface_status_fn


def _message(mid: str, subject: str, body: str, history: str) -> dict:
    return {
        "id": mid,
        "threadId": f"thread-{mid}",
        "historyId": history,
        "labelIds": ["INBOX"],
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": "founder@example.com"},
                {"name": "To", "value": "yohei@example.com"},
                {"name": "Date", "value": "2026-07-10T10:00:00+00:00"},
                {"name": "Message-ID", "value": f"<{mid}@example.com>"},
            ],
        },
        "messageText": body,
    }


class FakeComposio:
    def __init__(self):
        self.calls = []
        self.fail_next_fetch = False
        self.bad_shape_next_fetch = False
        self.rate_limit_next_history = False
        self.invalid_cursor_next_history = False

    def authorize(self, **kwargs):
        self.calls.append(("authorize", kwargs))
        return {
            "id": "link_1",
            "redirect_url": "https://connect.composio.dev/link/secret-token",
            "status": "INITIATED",
        }

    def list_connections(self, **kwargs):
        return [{"id": "ca_1", "status": "ACTIVE", "toolkit_slug": "gmail"}]

    def resolve_tool(self, *, toolkit, candidates, requested_version):
        self.calls.append(("resolve_tool", {
            "toolkit": toolkit,
            "candidates": tuple(candidates),
            "requested_version": requested_version,
        }))
        return resolve_catalog_tool(
            [{
                "slug": candidates[0],
                "toolkit": {"slug": toolkit},
                "version": "20260702_01",
                "available_versions": ["20260702_01"],
                "input_parameters": {"type": "object", "properties": {}},
                "is_deprecated": False,
            }],
            toolkit=toolkit,
            candidates=candidates,
            requested_version=requested_version,
        )

    def execute(self, **kwargs):
        self.calls.append((kwargs["tool_slug"], kwargs))
        slug = kwargs["tool_slug"]
        args = kwargs["arguments"]
        if slug == "GMAIL_GET_PROFILE":
            data = {"emailAddress": "yohei@example.com", "messagesTotal": 3, "threadsTotal": 3, "historyId": "900"}
        elif slug == "GMAIL_LIST_LABELS":
            data = {"labels": [{"id": "INBOX", "name": "INBOX", "type": "system"}]}
        elif slug == "GMAIL_FETCH_EMAILS":
            if self.fail_next_fetch:
                self.fail_next_fetch = False
                return {"successful": False, "error": "temporary upstream failure", "data": {}}
            if self.bad_shape_next_fetch:
                self.bad_shape_next_fetch = False
                data = {"unexpected": {"mail": "moved"}}
            elif args.get("page_token") == "page-2":
                data = {"messages": [_message("m2", "Second", "follow up", "900")]}
            else:
                data = {"messages": [_message("m1", "First", "hello", "899")], "nextPageToken": "page-2"}
        elif slug == "GMAIL_LIST_HISTORY":
            if self.rate_limit_next_history:
                self.rate_limit_next_history = False
                return {"successful": False, "error": "429 rate limit exceeded", "data": {}}
            if self.invalid_cursor_next_history:
                self.invalid_cursor_next_history = False
                return {"successful": False, "error": "404 historyId is too old", "data": {}}
            data = {
                "history": [{
                    "messagesAdded": [{"message": {"id": "m3"}}],
                    "messagesDeleted": [{"message": {"id": "m1"}}],
                }],
                "historyId": "901",
            }
        elif slug == "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID":
            data = _message("m3", "Third", "new mail", "901")
        else:
            data = {"id": "provider-result"}
        return {"successful": True, "data": {"response_data": data}, "error": None}


def _runtime(tmp_path, fake: FakeComposio):
    clear_local_registry()
    configure_composio_transport(fake)
    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    rt.load_pack(normalizer_pack, settings=ActivityNormalizerSettings(artifact_store_dir=str(tmp_path)))
    rt.load_pack(extraction_pack)
    rt.load_pack(usage_pack)
    rt.load_pack(gateway_pack)
    rt.load_pack(composio_pack)
    rt.load_pack(gmail_pack, settings=GmailSettings(artifact_store_dir=str(tmp_path)))
    register_composio_capabilities()
    register_gmail_capabilities(artifact_store_dir=str(tmp_path))
    return rt


def test_catalog_resolution_hardens_latest_and_rejects_an_invalid_pin():
    rows = [{
        "slug": "GMAIL_GET_PROFILE",
        "toolkit": {"slug": "gmail"},
        "version": "20260702_01",
        "available_versions": ["20260702_01"],
        "input_parameters": {"type": "object", "properties": {"user_id": {"type": "string"}}},
        "is_deprecated": False,
    }]
    resolved = resolve_catalog_tool(
        rows,
        toolkit="gmail",
        candidates=("GMAIL_GET_PROFILE_V2", "GMAIL_GET_PROFILE"),
        requested_version="latest",
    )
    assert resolved["tool_slug"] == "GMAIL_GET_PROFILE"
    assert resolved["version"] == "20260702_01"
    assert resolved["resolution"] == "catalog"
    assert resolved["input_schema_fingerprint"]

    with pytest.raises(ComposioUnavailable, match="available versions: 20260702_01"):
        resolve_catalog_tool(
            rows,
            toolkit="gmail",
            candidates=("GMAIL_GET_PROFILE",),
            requested_version="20260703_00",
        )


def test_connect_link_is_governed_and_secret_url_never_lands_in_log(tmp_path):
    fake = FakeComposio()
    rt = _runtime(tmp_path, fake)
    call = request_composio_link_fn(
        rt.graph,
        user_id="agent:owner",
        service="gmail",
        callback_url="http://127.0.0.1/callback",
    )
    rt.run_until_idle()
    assert rt.graph.get_object(call.id).data["status"] == "policy_checking"
    assert approve_capability_fn(rt.graph, call.id, "owner:client")["ok"] is True
    rt.run_until_idle()
    [result] = [obj for obj in rt.graph.objects(type="capability_result") if obj.data["call_id"] == call.id]
    payload = result.data["output_data"]
    assert "secret-token" not in payload
    assert "connect.composio.dev" in payload
    assert "[REDACTED" not in payload
    assert take_redirect("link_1") == "https://connect.composio.dev/link/secret-token"
    assert "secret-token" not in "\n".join(str(event.payload) for event in rt.graph.events)


def test_canonical_gmail_pack_accepts_a_non_composio_route_adapter(tmp_path):
    """Service semantics do not acquire a hard dependency on one route."""

    fake = FakeComposio()
    clear_local_registry()
    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    rt.load_pack(
        normalizer_pack,
        settings=ActivityNormalizerSettings(artifact_store_dir=str(tmp_path)),
    )
    rt.load_pack(extraction_pack)
    rt.load_pack(usage_pack)
    rt.load_pack(gateway_pack)
    rt.load_pack(gmail_pack, settings=GmailSettings(artifact_store_dir=str(tmp_path)))

    def execute_native(provider_operation, arguments, user_id, account_id, version):
        return fake.execute(
            tool_slug=provider_operation,
            arguments=arguments,
            user_id=user_id,
            connected_account_id=account_id,
            version=version,
        )

    register_gmail_capabilities(
        artifact_store_dir=str(tmp_path),
        route="native",
        execute_route=execute_native,
    )
    request_gmail_exploration_fn(
        rt.graph,
        user_id="agent:owner",
        connected_account_id="native-account-1",
        route="native",
    )
    rt.run_until_idle()

    [profile] = [
        obj for obj in rt.graph.objects(type="integration_profile")
        if obj.data["status"] == "active"
    ]
    [surface] = list(rt.graph.objects(type="connection_surface"))
    assert profile.data["routes"][0]["path"] == "native"
    assert surface.data["path"] == "native"
    assert not list(rt.graph.objects(type="aggregator_profile"))
    assert all(
        row["route"] == "native"
        for row in profile.data["capability_inventory"]
    )


def test_explore_backfill_poll_and_revoke_without_erasing_history(tmp_path):
    fake = FakeComposio()
    rt = _runtime(tmp_path, fake)

    exploration = request_gmail_exploration_fn(
        rt.graph, user_id="agent:owner", connected_account_id="ca_1"
    )
    rt.run_until_idle()
    assert exploration["created"] is True
    [profile] = [obj for obj in rt.graph.objects(type="integration_profile") if obj.data["status"] == "active"]
    assert profile.data["service"] == "gmail"
    assert profile.data["account_ref"] == "yohei@example.com"
    assert profile.data["routes"][0]["schema_version"] == "20260702_01"
    assert profile.data["routes"][0]["path"] == "composio"
    assert {row["operation"] for row in profile.data["capability_inventory"]} >= {"gmail.messages.fetch", "gmail.drafts.send"}
    assert {claim["claim_key"] for claim in profile.data["claims"]} >= {
        "account.identity", "mailbox.label_topology", "signal.inbox_richness",
    }
    [surface] = list(rt.graph.objects(type="connection_surface"))
    assert surface.data["path"] == "composio"
    assert surface.data["category"] == "communication"
    assert any(
        event.type == "source.connected"
        and (event.payload or {}).get("surface_id") == surface.data["surface_id"]
        for event in rt.graph.events
    )
    execution_rows = [
        payload for name, payload in fake.calls
        if name in {"GMAIL_GET_PROFILE", "GMAIL_LIST_LABELS"}
    ]
    assert execution_rows
    assert {row["version"] for row in execution_rows} == {"20260702_01"}

    # Status polling cannot rediscover the same account into endless profile
    # versions or duplicate structural probes.
    repeat = request_gmail_exploration_fn(
        rt.graph, user_id="agent:owner", connected_account_id="ca_1"
    )
    rt.run_until_idle()
    assert repeat["created"] is False
    assert len(list(rt.graph.objects(type="integration_profile"))) == 1

    run = request_gmail_backfill_fn(
        rt.graph,
        source_surface_id=surface.data["surface_id"],
        account_ref="yohei@example.com",
        user_id="agent:owner",
        connected_account_id="ca_1",
        query="newer_than:30d",
        page_size=1,
        max_messages=2,
        max_pages=2,
    )
    rt.run_until_idle()
    assert rt.graph.get_object(run["run_id"]).data["status"] == "completed"
    assert len(list(rt.graph.objects(type="activity_evidence"))) == 2
    assert not list(rt.graph.objects(type="profile_candidate"))
    assert not [
        event for event in rt.graph.events
        if event.type == "behavior.failed"
        and (event.payload or {}).get("behavior") == "gmail.gmail_sync_result_ingester"
    ]
    assert all(obj.data["connection_path"] == "composio" for obj in rt.graph.objects(type="activity_evidence"))
    assert all(obj.data["replay_mode"] == "artifact" for obj in rt.graph.objects(type="activity_evidence"))
    # Provider bodies live in artifacts, not gateway result events.
    gateway_outputs = "\n".join(obj.data["output_data"] for obj in rt.graph.objects(type="capability_result"))
    assert "hello" not in gateway_outputs and "follow up" not in gateway_outputs

    again = request_gmail_backfill_fn(
        rt.graph,
        source_surface_id=surface.data["surface_id"], account_ref="yohei@example.com",
        user_id="agent:owner", connected_account_id="ca_1",
        query="newer_than:30d", page_size=1, max_messages=2, max_pages=2,
    )
    rt.run_until_idle()
    assert again["created"] is False
    assert len(list(rt.graph.objects(type="activity_evidence"))) == 2

    poll = request_gmail_poll_fn(
        rt.graph,
        source_surface_id=surface.data["surface_id"], account_ref="yohei@example.com",
        user_id="agent:owner", connected_account_id="ca_1", start_history_id="900",
    )
    rt.run_until_idle()
    assert rt.graph.get_object(poll["run_id"]).data["status"] == "completed"
    assert len(list(rt.graph.objects(type="activity_evidence"))) == 3
    m1 = next(
        obj for obj in rt.graph.objects(type="activity_evidence")
        if obj.data.get("provider_item_id") == "m1"
    )
    assert m1.data["status"] == "revoked"
    [tombstone] = list(rt.graph.objects(type="evidence_invalidation_request"))
    assert tombstone.data["status"] == "fulfilled"
    assert tombstone.data["invalidated_evidence_ids"] == [m1.id]
    m1_usage = next(
        obj for obj in rt.graph.objects(type="usage_evidence")
        if obj.data.get("evidence_id") == m1.id
    )
    assert m1_usage.data["invalidated"] is True
    [cursor] = list(rt.graph.objects(type="backfill_cursor"))
    assert cursor.data["watermark_ref"] == "history:901"

    set_surface_status_fn(rt.graph, surface.data["surface_id"], "revoked", reason="owner revoked OAuth")
    rt.run_until_idle()
    assert len(list(rt.graph.objects(type="activity_evidence"))) == 3
    assert list(rt.graph.objects(type="connection_surface"))[0].data["status"] == "revoked"


def test_interrupted_backfill_restarts_with_overlap_and_dedups(tmp_path):
    fake = FakeComposio()
    rt = _runtime(tmp_path, fake)
    request_gmail_exploration_fn(rt.graph, user_id="agent:owner", connected_account_id="ca_1")
    rt.run_until_idle()
    [surface] = list(rt.graph.objects(type="connection_surface"))
    fake.fail_next_fetch = True
    first = request_gmail_backfill_fn(
        rt.graph, source_surface_id=surface.data["surface_id"], account_ref="yohei@example.com",
        user_id="agent:owner", connected_account_id="ca_1", page_size=1, max_messages=2, max_pages=2,
    )
    rt.run_until_idle()
    assert rt.graph.get_object(first["run_id"]).data["status"] == "failed"
    resumed = request_gmail_backfill_fn(
        rt.graph, source_surface_id=surface.data["surface_id"], account_ref="yohei@example.com",
        user_id="agent:owner", connected_account_id="ca_1", page_size=1, max_messages=2, max_pages=2,
    )
    rt.run_until_idle()
    assert resumed["resumed"] is True
    assert rt.graph.get_object(first["run_id"]).data["status"] == "completed"
    assert len(list(rt.graph.objects(type="activity_evidence"))) == 2


def test_bound_hit_is_partial_and_requires_an_explicit_deeper_window(tmp_path):
    rt = _runtime(tmp_path, FakeComposio())
    request_gmail_exploration_fn(rt.graph, user_id="agent:owner", connected_account_id="ca_1")
    rt.run_until_idle()
    [surface] = list(rt.graph.objects(type="connection_surface"))
    first = request_gmail_backfill_fn(
        rt.graph,
        source_surface_id=surface.data["surface_id"],
        account_ref="yohei@example.com",
        user_id="agent:owner",
        connected_account_id="ca_1",
        page_size=1,
        max_messages=1,
        max_pages=1,
    )
    rt.run_until_idle()
    assert rt.graph.get_object(first["run_id"]).data["status"] == "partial"
    same = request_gmail_backfill_fn(
        rt.graph,
        source_surface_id=surface.data["surface_id"],
        account_ref="yohei@example.com",
        user_id="agent:owner",
        connected_account_id="ca_1",
        page_size=1,
        max_messages=1,
        max_pages=1,
    )
    assert same["created"] is False and same["run_id"] == first["run_id"]
    assert len(list(rt.graph.objects(type="gmail_sync_run"))) == 1


def test_local_draft_is_r1_and_send_is_never_auto_run(tmp_path):
    fake = FakeComposio()
    rt = _runtime(tmp_path, fake)
    draft, created = create_gmail_draft_candidate_fn(
        rt.graph,
        account_ref="yohei@example.com", connected_account_id="ca_1",
        to=["person@example.com"], subject="Hello", body="Draft body",
    )
    rt.run_until_idle()
    assert created is True and draft.data["status"] == "local_draft"
    synced = request_gmail_draft_sync_fn(
        rt.graph, draft_id=draft.id, user_id="agent:owner"
    )
    rt.run_until_idle()
    sync_call = rt.graph.get_object(synced["call_id"])
    assert sync_call.data["action_class"] == "R2"
    assert sync_call.data["status"] == "policy_checking"
    assert approve_capability_fn(rt.graph, sync_call.id, "owner:client")["ok"] is True
    rt.run_until_idle()
    draft = rt.graph.get_object(draft.id)
    assert draft.data["status"] == "synced"
    assert draft.data["provider_draft_id"] == "provider-result"

    proposed = request_gmail_draft_send_fn(
        rt.graph, draft_id=draft.id, user_id="agent:owner"
    )
    rt.run_until_idle()
    send = rt.graph.get_object(proposed["call_id"])
    assert send.data["action_class"] == "R3"
    assert send.data["status"] == "policy_checking"
    assert not [obj for obj in rt.graph.objects(type="capability_result") if obj.data["call_id"] == send.id]
    # Re-proposing is idempotent and cannot bypass the held R3 call.
    again = request_gmail_draft_send_fn(rt.graph, draft_id=draft.id, user_id="agent:owner")
    assert again["created"] is False and again["call_id"] == send.id


def test_unexpected_shape_records_drift_and_forced_reexploration(tmp_path):
    fake = FakeComposio()
    rt = _runtime(tmp_path, fake)
    request_gmail_exploration_fn(rt.graph, user_id="agent:owner", connected_account_id="ca_1")
    rt.run_until_idle()
    [surface] = list(rt.graph.objects(type="connection_surface"))
    fake.bad_shape_next_fetch = True
    started = request_gmail_backfill_fn(
        rt.graph,
        source_surface_id=surface.data["surface_id"],
        account_ref="yohei@example.com",
        user_id="agent:owner",
        connected_account_id="ca_1",
        max_messages=1,
        max_pages=1,
    )
    rt.run_until_idle()
    run = rt.graph.get_object(started["run_id"])
    assert run.data["status"] == "failed"
    assert run.data["error_code"] == "unexpected_shape"
    assert any(
        obj.data.get("error_code") == "gmail.unexpected_shape"
        for obj in rt.graph.objects(type="ingestion_failure")
    )
    latest = sorted(
        rt.graph.objects(type="integration_profile"),
        key=lambda obj: obj.data["profile_version"],
    )[-1]
    assert latest.data["status"] == "stale"
    assert latest.data["health"]["drift_reason"] == "unexpected_shape"
    assert all(claim["freshness"] == "stale" for claim in latest.data["claims"])

    refreshed = request_gmail_exploration_fn(
        rt.graph,
        user_id="agent:owner",
        connected_account_id="ca_1",
        force=True,
    )
    rt.run_until_idle()
    assert refreshed["created"] is True
    current = sorted(
        rt.graph.objects(type="integration_profile"),
        key=lambda obj: obj.data["profile_version"],
    )[-1]
    assert current.data["status"] == "active"
    assert current.data["profile_version"] == 3


def test_rate_limit_retries_but_invalid_cursor_requires_reanchor(tmp_path):
    fake = FakeComposio()
    rt = _runtime(tmp_path, fake)
    request_gmail_exploration_fn(rt.graph, user_id="agent:owner", connected_account_id="ca_1")
    rt.run_until_idle()
    [surface] = list(rt.graph.objects(type="connection_surface"))

    fake.rate_limit_next_history = True
    first = request_gmail_poll_fn(
        rt.graph,
        source_surface_id=surface.data["surface_id"],
        account_ref="yohei@example.com",
        user_id="agent:owner",
        connected_account_id="ca_1",
        start_history_id="900",
    )
    rt.run_until_idle()
    assert rt.graph.get_object(first["run_id"]).data["error_code"] == "rate_limited"
    retried = request_gmail_poll_fn(
        rt.graph,
        source_surface_id=surface.data["surface_id"],
        account_ref="yohei@example.com",
        user_id="agent:owner",
        connected_account_id="ca_1",
        start_history_id="900",
    )
    rt.run_until_idle()
    assert retried["resumed"] is True
    assert rt.graph.get_object(first["run_id"]).data["status"] == "completed"

    fake.invalid_cursor_next_history = True
    invalid = request_gmail_poll_fn(
        rt.graph,
        source_surface_id=surface.data["surface_id"],
        account_ref="yohei@example.com",
        user_id="agent:owner",
        connected_account_id="ca_1",
        start_history_id="899",
    )
    rt.run_until_idle()
    run = rt.graph.get_object(invalid["run_id"])
    assert run.data["error_code"] == "cursor_invalid"
    assert run.data["metadata"]["reanchor_required"] is True


def test_owner_correction_supersedes_an_integration_claim(tmp_path):
    rt = _runtime(tmp_path, FakeComposio())
    request_gmail_exploration_fn(rt.graph, user_id="agent:owner", connected_account_id="ca_1")
    rt.run_until_idle()
    profile = next(
        obj for obj in rt.graph.objects(type="integration_profile")
        if obj.data["status"] == "active"
    )
    corrected = correct_integration_claim_fn(
        rt.graph,
        profile_id=profile.id,
        claim_key="signal.inbox_richness",
        value="medium",
        actor="owner:test",
        reason="this account is mostly receipts",
    )
    current = rt.graph.get_object(corrected["profile_id"])
    claim = next(
        row for row in current.data["claims"]
        if row["claim_key"] == "signal.inbox_richness"
    )
    assert profile.data["status"] == "superseded"
    assert current.data["profile_version"] == 2
    assert claim["classification_source"] == "operator"
    assert claim["value"] == "medium"
    assert claim["metadata"]["reason"] == "this account is mostly receipts"
