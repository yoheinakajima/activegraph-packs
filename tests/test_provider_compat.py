"""Unit tests for the provider-boundary compatibility layer (packs/chat/llm.py).

ProviderCompat's contract: canonical pack-scoped tool names ('pack.tool')
everywhere inside the framework, wire-legal names ('pack__tool') on the
provider API, translated back on the way in. _OpenAIParamShim's contract:
reasoning-family models get max_completion_tokens and no temperature/top_p;
every other model's payload passes through byte-identical.

These are direct unit tests (no subprocess fixture) because the seam is a
pure function boundary — no graph, no runtime, no API key.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from dataclasses import dataclass, field
from typing import Any, Optional

from activegraph.llm.types import LLMMessage, LLMResponse, ToolCall

from packs.chat.llm import (
    FallbackChatProvider,
    ProviderCompat,
    _is_reasoning_family,
    _OpenAIParamShim,
    _safe_tool_name,
)


# ------------------------------------------------------------------ stubs


@dataclass
class _StubProvider:
    """Records the kwargs it was called with; returns a scripted response."""

    response: LLMResponse
    seen: dict[str, Any] = field(default_factory=dict)
    default_model: str = "stub-1"

    def complete(self, **kwargs: Any) -> LLMResponse:
        self.seen = kwargs
        return self.response

    def estimate_cost(self, **kwargs: Any) -> Decimal:
        return Decimal("0")

    def count_tokens(self, **kwargs: Any) -> int:
        return 0

    def recognizes_model(self, name: str) -> bool:
        return True


def _response(tool_calls: Optional[list[ToolCall]] = None) -> LLMResponse:
    return LLMResponse(
        raw_text="ok",
        parsed=None,
        input_tokens=0,
        output_tokens=0,
        cost_usd=Decimal("0"),
        latency_seconds=0.0,
        model="stub-1",
        finish_reason="stop",
        tool_calls=tool_calls,
    )


# ------------------------------------------------------------------ ProviderCompat


def test_scoped_tool_names_sanitized_outbound_and_restored_inbound():
    stub = _StubProvider(
        response=_response(
            tool_calls=[ToolCall(id="c1", name="my_pack__do_thing", args={"x": 1})]
        )
    )
    compat = ProviderCompat(stub)

    resp = compat.complete(
        system="",
        messages=[LLMMessage(role="user", content="hi")],
        model="gpt-4o-mini",
        max_tokens=100,
        temperature=0.7,
        top_p=1.0,
        output_schema=None,
        timeout_seconds=30.0,
        tools=[{"name": "my_pack.do_thing", "description": "d", "input_schema": {}}],
    )

    # Outbound: the wire saw the sanitized name; canonical name nowhere on the wire.
    assert stub.seen["tools"][0]["name"] == "my_pack__do_thing"
    # Inbound: the runtime sees the canonical name again.
    assert resp.tool_calls[0].name == "my_pack.do_thing"
    assert resp.tool_calls[0].args == {"x": 1}


def test_prior_turn_assistant_tool_calls_sanitized_without_mutation():
    stub = _StubProvider(response=_response())
    compat = ProviderCompat(stub)

    assistant_msg = LLMMessage(
        role="assistant",
        content="",
        tool_calls=[ToolCall(id="c0", name="my_pack.do_thing", args={})],
    )
    compat.complete(
        system="",
        messages=[assistant_msg],
        model="gpt-4o-mini",
        max_tokens=100,
        temperature=0.7,
        top_p=1.0,
        output_schema=None,
        timeout_seconds=30.0,
        tools=[{"name": "my_pack.do_thing", "description": "d", "input_schema": {}}],
    )

    sent = stub.seen["messages"][0]
    assert sent.tool_calls[0].name == "my_pack__do_thing"
    # The runtime owns the original message objects — never mutated.
    assert assistant_msg.tool_calls[0].name == "my_pack.do_thing"


def test_unscoped_names_and_toolless_calls_pass_through():
    stub = _StubProvider(
        response=_response(tool_calls=[ToolCall(id="c1", name="plain_tool", args={})])
    )
    compat = ProviderCompat(stub)

    resp = compat.complete(
        system="",
        messages=[],
        model="gpt-4o-mini",
        max_tokens=100,
        temperature=0.7,
        top_p=1.0,
        output_schema=None,
        timeout_seconds=30.0,
        tools=[{"name": "plain_tool", "description": "d", "input_schema": {}}],
    )
    assert stub.seen["tools"][0]["name"] == "plain_tool"
    assert resp.tool_calls[0].name == "plain_tool"

    # No tools → kwargs pass through untouched (same messages object).
    msgs = [LLMMessage(role="user", content="hi")]
    compat.complete(
        system="",
        messages=msgs,
        model="gpt-4o-mini",
        max_tokens=100,
        temperature=0.7,
        top_p=1.0,
        output_schema=None,
        timeout_seconds=30.0,
    )
    assert stub.seen["messages"] is msgs


def test_hallucinated_tool_name_passes_through_unmapped():
    stub = _StubProvider(
        response=_response(tool_calls=[ToolCall(id="c1", name="not_a_tool", args={})])
    )
    compat = ProviderCompat(stub)
    resp = compat.complete(
        system="",
        messages=[],
        model="gpt-4o-mini",
        max_tokens=100,
        temperature=0.7,
        top_p=1.0,
        output_schema=None,
        timeout_seconds=30.0,
        tools=[{"name": "my_pack.do_thing", "description": "d", "input_schema": {}}],
    )
    # Unknown names are the runtime's error to raise, not ours to guess at.
    assert resp.tool_calls[0].name == "not_a_tool"


def test_safe_tool_name():
    assert _safe_tool_name("pack.tool") == "pack__tool"
    assert _safe_tool_name("plain") == "plain"


# ------------------------------------------------------------------ _OpenAIParamShim


class _StubOpenAIClient:
    def __init__(self) -> None:
        self.seen: dict[str, Any] = {}

        import types

        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs: Any) -> Any:
        self.seen = kwargs
        return object()


def test_reasoning_family_detection():
    assert _is_reasoning_family("gpt-5")
    assert _is_reasoning_family("gpt-5-mini")
    assert _is_reasoning_family("o3-mini")
    assert not _is_reasoning_family("gpt-4o-mini")
    assert not _is_reasoning_family("gpt-4.1")


def test_param_shim_translates_for_reasoning_models_only():
    inner = _StubOpenAIClient()
    shim = _OpenAIParamShim(inner)

    shim.chat.completions.create(model="gpt-5-mini", max_tokens=256, temperature=0.7)
    assert inner.seen == {"model": "gpt-5-mini", "max_completion_tokens": 256}

    shim.chat.completions.create(model="gpt-4o-mini", max_tokens=256, temperature=0.7)
    assert inner.seen == {"model": "gpt-4o-mini", "max_tokens": 256, "temperature": 0.7}


# ------------------------------------------------------------------ FallbackChatProvider


class _ExplodingProvider(_StubProvider):
    def complete(self, **kwargs: Any) -> LLMResponse:
        raise ValueError("model `nope-9` does not exist (HTTP 400)")


def test_fallback_names_the_real_error_on_plain_chat():
    fallback = FallbackChatProvider(
        _ExplodingProvider(response=_response()), provider="openai", key_env="OPENAI_API_KEY"
    )
    resp = fallback.complete(
        system="",
        messages=[],
        model="nope-9",
        max_tokens=100,
        temperature=0.7,
        top_p=1.0,
        output_schema=None,
        timeout_seconds=30.0,
    )
    assert "ValueError" in resp.raw_text
    assert "nope-9" in resp.raw_text


def test_fallback_degrades_on_first_call_even_with_tools_offered():
    # No tool interaction has happened yet — a canned reply is safe and
    # better UX than a dead turn.
    fallback = FallbackChatProvider(
        _ExplodingProvider(response=_response()), provider="openai", key_env="OPENAI_API_KEY"
    )
    resp = fallback.complete(
        system="",
        messages=[LLMMessage(role="user", content="hi")],
        model="nope-9",
        max_tokens=100,
        temperature=0.7,
        top_p=1.0,
        output_schema=None,
        timeout_seconds=30.0,
        tools=[{"name": "t", "description": "", "input_schema": {}}],
    )
    assert "ValueError" in resp.raw_text


def test_fallback_reraises_mid_tool_loop():
    # Prior tool activity exists — canned text would silently replace a
    # grounded answer, so the error must surface.
    fallback = FallbackChatProvider(
        _ExplodingProvider(response=_response()), provider="openai", key_env="OPENAI_API_KEY"
    )
    import pytest

    mid_loop_messages = [
        LLMMessage(role="user", content="hi"),
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="c1", name="t", args={})],
        ),
        LLMMessage(role="tool", content="{}", tool_use_id="c1"),
    ]
    with pytest.raises(ValueError):
        fallback.complete(
            system="",
            messages=mid_loop_messages,
            model="nope-9",
            max_tokens=100,
            temperature=0.7,
            top_p=1.0,
            output_schema=None,
            timeout_seconds=30.0,
            tools=[{"name": "t", "description": "", "input_schema": {}}],
        )
