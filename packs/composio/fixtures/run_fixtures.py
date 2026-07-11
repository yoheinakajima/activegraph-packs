from activegraph import Graph, Runtime

from packs.composio import pack
from packs.composio.capabilities import register_composio_capabilities
from packs.composio.client import configure_composio_transport, take_redirect
from packs.composio.tools import request_composio_link_fn
from packs.core import pack as core_pack
from packs.tool_gateway import pack as gateway_pack
from packs.tool_gateway.tools import approve_capability_fn, clear_local_registry


class FixtureTransport:
    def authorize(self, **kwargs):
        return {"id": "conn_req_fixture", "redirect_url": "https://connect.composio.dev/link/fixture", "status": "INITIATED"}

    def list_connections(self, **kwargs):
        return [{"id": "ca_fixture", "status": "ACTIVE", "toolkit_slug": kwargs["toolkit"]}]

    def execute(self, **kwargs):
        return {"successful": True, "data": {}}


def main():
    clear_local_registry()
    configure_composio_transport(FixtureTransport())
    runtime = Runtime(Graph())
    runtime.load_pack(core_pack)
    runtime.load_pack(gateway_pack)
    runtime.load_pack(pack)
    register_composio_capabilities()
    call = request_composio_link_fn(
        runtime.graph,
        user_id="fixture:owner",
        service="gmail",
        callback_url="http://localhost/return",
    )
    runtime.run_until_idle()
    assert approve_capability_fn(runtime.graph, call.id, "fixture:owner")["ok"]
    runtime.run_until_idle()
    result = next(
        obj for obj in runtime.graph.objects(type="capability_result")
        if obj.data["call_id"] == call.id
    )
    assert "connect.composio.dev" in result.data["output_data"]
    assert "/link/fixture" not in result.data["output_data"]
    assert take_redirect("conn_req_fixture").endswith("/link/fixture")
    configure_composio_transport(None)
    clear_local_registry()
    print("composio fixtures: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
