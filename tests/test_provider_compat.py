"""Shim-retirement proof: the runtime owns the provider wire boundary.

Until activegraph v1.3, this repo carried two shims in packs/chat/llm.py:
ProviderCompat (pack-scoped tool names sanitized to the wire alphabet and
reverse-mapped on the way back) and an OpenAI reasoning-family parameter
shim (max_tokens → max_completion_tokens, temperature/top_p dropped).
CONTRACT v1.3 #3 moved both into the runtime. Per the shim-retirement
rule — every shim dies with proof — these tests point at the REAL runtime
surfaces that replaced the shims. If any of them fails on a future
runtime, the wire boundary regressed and the pin (or the shim) must come
back.

Also covers the error-taxonomy split the old shim's honest-fallback text
worked around: auth/request errors are terminal reason codes now, no
longer retried as network flakes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from activegraph.llm.openai import OpenAIProvider
from activegraph.llm.wire import (
    build_tool_name_map,
    classify_provider_exception,
    restore_tool_name,
    sanitize_tool_name,
)


# ------------------------------------------------- tool-name wire boundary


def test_pack_scoped_names_sanitize_and_round_trip():
    """'pack.tool' → 'pack__tool' on the wire, restored exactly."""
    canonical = "diligence.fetch_filings"
    wire_name = sanitize_tool_name(canonical)
    assert wire_name == "diligence__fetch_filings"
    name_map = build_tool_name_map([{"name": canonical}, {"name": "web.fetch_url"}])
    assert restore_tool_name(wire_name, name_map) == canonical
    assert restore_tool_name(sanitize_tool_name("web.fetch_url"), name_map) == "web.fetch_url"


def test_wire_safe_names_pass_through_unchanged():
    """Non-pack tool names stay byte-identical (pre-v1.3 requests intact)."""
    assert sanitize_tool_name("fetch_url") == "fetch_url"
    assert sanitize_tool_name("tool-name_2") == "tool-name_2"


def test_gateway_capability_keys_are_wire_translatable():
    """The exact names this repo puts on allow-lists survive the boundary:
    the gateway's canonical 'provider.capability' keys and MCP-derived
    'mcp_<server>.<tool>' keys."""
    for key in ["catalog.search", "mcp.set_exposure", "mcp_github.create_issue",
                "schedule.create_reminder"]:
        wire_name = sanitize_tool_name(key)
        assert "." not in wire_name
        assert restore_tool_name(wire_name, build_tool_name_map([{"name": key}])) == key


def test_unmapped_response_names_pass_through():
    """A hallucinated tool name with no mapping is not guessed at — the
    runtime's unknown-tool handling owns that error (the old shim's rule,
    preserved by the runtime)."""
    assert restore_tool_name("made_up_tool", build_tool_name_map([{"name": "a.b"}])) == "made_up_tool"


# ------------------------------------------------- reasoning-family params


def test_runtime_owns_reasoning_family_params():
    """OpenAIProvider recognizes the reasoning families the retired shim
    covered (and gpt-5), and exposes the prefix seam the shim lacked."""
    provider = OpenAIProvider()
    for model in ["o1-mini", "o3-large", "o4-mini", "gpt-5-turbo"]:
        assert provider._is_reasoning_model(model), model
    assert not provider._is_reasoning_model("gpt-4o-mini")
    # The seam the shim never had: operator-extendable prefixes.
    custom = OpenAIProvider(reasoning_model_prefixes=("o1", "future-family"))
    assert custom._is_reasoning_model("future-family-1")


# ------------------------------------------------- error taxonomy split


class _FakeAuthError(Exception):
    status_code = 401


class _FakeBadRequest(Exception):
    status_code = 400


class _FakeRateLimit(Exception):
    status_code = 429


class _FakeTimeout(Exception):
    pass


def test_terminal_errors_are_no_longer_network_flakes():
    """A revoked key or an invalid request is terminal, not retried with
    backoff and blamed on the network (the pre-v1.3 failure mode)."""
    assert classify_provider_exception(_FakeAuthError("bad key")) == "llm.auth_error"
    assert classify_provider_exception(_FakeBadRequest("bad param")) == "llm.request_error"
    assert classify_provider_exception(_FakeRateLimit("slow down")) == "llm.rate_limited"
    # Unknown shapes keep the transient code (pre-v1.3 retry behavior).
    assert classify_provider_exception(_FakeTimeout("timeout")) == "llm.network_error"


def test_no_production_code_reads_response_text_directly():
    """Static regression for the first live keyed run: production provider
    seams must read through response_text(), never a bare .text that a
    simplified fake happens to satisfy. llm_provider.py is the seam and
    documents the hazard."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "packs"
    offenders = []
    for source in root.rglob("*.py"):
        if source.name == "llm_provider.py" or "fixtures" in source.parts:
            continue
        text = source.read_text(encoding="utf-8")
        if 'getattr(response, "text"' in text or re.search(
            r"\bresponse\.text\b", text
        ):
            offenders.append(str(source.relative_to(root)))
    assert offenders == [], f"raw response.text reads: {offenders}"
