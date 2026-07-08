"""Gateway-side registration enforcement (Q8 chain step 3).

Armed with a live graph, register_local_capability refuses undeclared
pairs, risk-class drift, and disabled packs' surfaces; unarmed and
MCP-origin registrations behave as before. The CI AST check
(test_manifests) is the review-time half; this is the runtime half.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from activegraph import Graph, Runtime
from activegraph.packs import Pack
from activegraph.packs.manifest import CapabilityDecl

from packs.tool_gateway import pack as tg_pack, ToolGatewaySettings
from packs.tool_gateway.registration_check import (
    arm_registration_enforcement,
    disarm_registration_enforcement,
)
from packs.tool_gateway.tools import (
    clear_local_registry,
    register_local_capability,
)


class _In(BaseModel):
    pass


def _noop() -> dict:
    return {}


@pytest.fixture()
def rt():
    clear_local_registry()
    disarm_registration_enforcement()
    runtime = Runtime(Graph())
    runtime.load_pack(tg_pack, settings=ToolGatewaySettings())
    yield runtime
    disarm_registration_enforcement()
    clear_local_registry()


def _declaring_pack(name: str = "declarer", risk: str = "high") -> Pack:
    return Pack(
        name=name,
        version="0.1.0",
        description="test pack that declares one capability",
        object_types=(),
        relation_types=(),
        behaviors=(),
        tools=(),
        policies=(),
        prompts=(),
        capabilities=(
            CapabilityDecl(provider="declarer", capability="do_thing",
                           risk_class=risk, credential_ref=""),
        ),
    )


def test_unarmed_registration_is_unchecked(rt):
    register_local_capability("rogue", "anything", _noop, input_schema=_In,
                              risk_class="low")


def test_undeclared_pair_refused(rt):
    arm_registration_enforcement(rt.graph)
    with pytest.raises(ValueError, match="no loaded pack declares"):
        register_local_capability("rogue", "exfiltrate", _noop,
                                  input_schema=_In, risk_class="low")


def test_declared_pair_registers(rt):
    rt.load_pack(_declaring_pack())
    arm_registration_enforcement(rt.graph)
    spec = register_local_capability("declarer", "do_thing", _noop,
                                     input_schema=_In, risk_class="high")
    assert spec.key == "declarer.do_thing"


def test_risk_class_drift_refused(rt):
    """The swap the decision surface must not miss: declared high,
    registered low (or any mismatch, either direction)."""
    rt.load_pack(_declaring_pack(risk="high"))
    arm_registration_enforcement(rt.graph)
    with pytest.raises(ValueError, match="[Rr]isk drift"):
        register_local_capability("declarer", "do_thing", _noop,
                                  input_schema=_In, risk_class="low")
    # The gateway's own declared pair drifts the other way.
    with pytest.raises(ValueError, match="[Rr]isk drift"):
        register_local_capability("web", "fetch_url", _noop,
                                  input_schema=_In, risk_class="critical")


def test_disabled_pack_surface_refused(rt):
    rt.load_pack(_declaring_pack())
    rt.disable_pack("declarer")
    arm_registration_enforcement(rt.graph)
    with pytest.raises(ValueError, match="disabled"):
        register_local_capability("declarer", "do_thing", _noop,
                                  input_schema=_In, risk_class="high")


def test_reload_after_disable_re_enables(rt):
    """Latest pack event wins: a fresh load after a disable is a fresh
    adoption, and registration works again."""
    rt.load_pack(_declaring_pack())
    rt.disable_pack("declarer")
    rt.load_pack(_declaring_pack())
    arm_registration_enforcement(rt.graph)
    register_local_capability("declarer", "do_thing", _noop,
                              input_schema=_In, risk_class="high")


def test_mcp_origin_exempt(rt):
    """MCP-origin registrations are host-mediated and governed by
    exposure rules, never pack manifests."""
    arm_registration_enforcement(rt.graph)
    register_local_capability("some_server", "remote_tool", _noop,
                              input_schema=_In, risk_class="high",
                              origin="mcp:some_server")


def test_armed_boot_path_still_registers_declared_capabilities(rt):
    """The gateway's own web.fetch_url and catalog.search are declared
    by the tool_gateway pack; arming must not break them."""
    from packs.tool_gateway.capabilities import register_web_fetch_capability
    from packs.tool_gateway.catalog import register_catalog_capability

    arm_registration_enforcement(rt.graph)
    register_web_fetch_capability()
    register_catalog_capability(lambda: [])
