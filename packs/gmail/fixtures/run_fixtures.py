from pathlib import Path
from tempfile import TemporaryDirectory

from activegraph import Graph, Runtime

from packs.activity_normalizer import ActivityNormalizerSettings, pack as normalizer_pack
from packs.composio import pack as composio_pack
from packs.composio.client import configure_composio_transport
from packs.core import pack as core_pack
from packs.gmail import GmailSettings, pack as gmail_pack
from packs.gmail.capabilities import register_gmail_capabilities
from packs.gmail.tools import request_gmail_backfill_fn, request_gmail_exploration_fn
from packs.semantic_extraction import pack as extraction_pack
from packs.tool_gateway import pack as gateway_pack
from packs.tool_gateway.tools import clear_local_registry
from packs.usage import pack as usage_pack


class FixtureTransport:
    def authorize(self, **kwargs):
        raise AssertionError("fixture never authorizes")

    def list_connections(self, **kwargs):
        return [{"id": "ca_fixture", "status": "ACTIVE", "toolkit_slug": "gmail"}]

    def execute(self, **kwargs):
        slug = kwargs["tool_slug"]
        if slug == "GMAIL_GET_PROFILE":
            data = {"emailAddress": "fixture@example.com", "historyId": "10"}
        elif slug == "GMAIL_LIST_LABELS":
            data = {"labels": [{"id": "INBOX", "name": "INBOX"}]}
        elif slug == "GMAIL_FETCH_EMAILS":
            data = {
                "messages": [{
                    "id": "m_fixture",
                    "threadId": "t_fixture",
                    "historyId": "10",
                    "labelIds": ["INBOX"],
                    "payload": {"headers": [{"name": "Subject", "value": "Fixture"}]},
                    "messageText": "fixture body",
                }]
            }
        else:
            data = {}
        return {"successful": True, "data": {"response_data": data}, "error": None}


def main():
    clear_local_registry()
    configure_composio_transport(FixtureTransport())
    with TemporaryDirectory() as directory:
        artifacts = str(Path(directory) / "artifacts")
        runtime = Runtime(Graph())
        runtime.load_pack(core_pack)
        runtime.load_pack(normalizer_pack, settings=ActivityNormalizerSettings(artifact_store_dir=artifacts))
        runtime.load_pack(extraction_pack)
        runtime.load_pack(usage_pack)
        runtime.load_pack(gateway_pack)
        runtime.load_pack(composio_pack)
        runtime.load_pack(gmail_pack, settings=GmailSettings(artifact_store_dir=artifacts))
        register_gmail_capabilities(artifact_store_dir=artifacts)
        request_gmail_exploration_fn(
            runtime.graph, user_id="fixture:owner", connected_account_id="ca_fixture"
        )
        runtime.run_until_idle()
        surface = list(runtime.graph.objects(type="connection_surface"))[0]
        request_gmail_backfill_fn(
            runtime.graph,
            source_surface_id=surface.data["surface_id"],
            account_ref="fixture@example.com",
            user_id="fixture:owner",
            connected_account_id="ca_fixture",
            max_messages=1,
            max_pages=1,
        )
        runtime.run_until_idle()
        assert len(list(runtime.graph.objects(type="integration_profile"))) == 1
        assert len(list(runtime.graph.objects(type="activity_evidence"))) == 1
        assert not runtime.errors
    configure_composio_transport(None)
    clear_local_registry()
    print("gmail fixtures: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
