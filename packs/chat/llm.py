"""Chat Adapter Pack — native LLM wiring.

This module connects the Chat Pack to ActiveGraph's *native* LLM layer
(``activegraph.llm``) rather than hand-rolling provider HTTP calls.

Pieces:

  ChatReply           — the structured output schema for chat_llm_responder.
                        The model returns ``{"reply": "..."}`` and the
                        behavior writes ``reply`` onto the ChatTurn.

  MockChatProvider    — a scripted ``LLMProvider`` used when no real provider
                        key is configured. It runs the full chat pipeline
                        end-to-end (so the demo / fixtures work with no API
                        key) and its reply explains how to enable a real LLM.

  select_chat_provider — inspects the environment and returns the right
                        provider instance (OpenAIProvider / AnthropicProvider
                        when a key is present, MockChatProvider otherwise) plus
                        an info dict describing the resolved configuration.

SECURITY: API keys are read from the environment by the native providers at
call time. They are never written to the graph, events, logs, or artifacts.
The Secrets Pack records name-only CredentialRefs for auditability.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field

from activegraph.llm import AnthropicProvider, LLMResponse, OpenAIProvider
from activegraph.llm.types import ToolCall


# ================================================================ Output schema


class ChatReply(BaseModel):
    """Structured output for chat_llm_responder.

    A single free-text assistant reply. Kept deliberately minimal so the
    model's job is unambiguous: produce the assistant's next message.
    """

    reply: str = Field(
        description="The assistant's reply to the user's most recent message.",
    )


# ================================================================ Provider config

# Chat providers, keyed by the short provider id used in settings / the config
# API. OpenAI and Anthropic use their native ActiveGraph providers.
_PROVIDER_KEY_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

# Order used when auto-detecting which provider to use from the environment.
_AUTODETECT_ORDER = ["openai", "anthropic"]

# Effective default model per provider when none is configured.
_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-5",
}

# Provider ids chat can use.
SUPPORTED_PROVIDERS = list(_PROVIDER_KEY_ENV.keys())


def provider_key_env(provider: str) -> Optional[str]:
    """Return the env var name a provider's API key is read from, or None."""
    return _PROVIDER_KEY_ENV.get((provider or "").strip().lower())


_DEFAULT_MOCK_NOTE = (
    "I'm running in mock mode, so this is a canned reply — but the full "
    "ActiveGraph chat pipeline did run end-to-end (ingest \u2192 comm message "
    "\u2192 responder \u2192 turn), only the LLM itself is stubbed.\n\n"
    "To get real answers, add a provider API key:\n"
    "  \u2022 OPENAI_API_KEY \u2014 use OpenAI\n"
    "  \u2022 ANTHROPIC_API_KEY \u2014 use Anthropic\n\n"
    "Set it as an environment variable / Replit Secret, or add it on the "
    "Secrets page in this Inspector. As soon as a key is detected, chat "
    "upgrades to live mode automatically."
)


def _live_error_note(provider: str, key_env: Optional[str], error: Optional[Exception] = None) -> str:
    """Instructive fallback text shown when a live provider call fails.

    Includes the actual exception class and message — a 400 from a bad
    model name must not read like a network problem.
    """
    key = key_env or "the provider API key"
    detail = ""
    if error is not None:
        msg = str(error)
        if len(msg) > 300:
            msg = msg[:300] + "…"
        detail = f"\n\nThe underlying error was: {type(error).__name__}: {msg}"
    return (
        f"The live {provider} call failed, so this is a mock fallback reply — "
        "the full ActiveGraph chat pipeline still ran end-to-end; only the "
        f"LLM call itself errored.{detail}\n\n"
        f"Check that {key} holds a valid key and that the configured model "
        "name is correct, then try again. You can update the key or model on "
        "the Secrets page in this Inspector. Chat returns to live mode "
        "automatically once a working key is in place."
    )


class MockChatProvider:
    """A scripted ``LLMProvider`` for the no-API-key path.

    Implements the ``activegraph.llm.LLMProvider`` protocol surface so the
    runtime's native LLM lifecycle (``llm.requested`` \u2192 provider \u2192
    ``llm.responded``) works unchanged — the only difference is that the
    completion is canned instead of a network call.

    The reply is the same instructive text every time, which keeps fixtures
    deterministic while telling demo users exactly how to enable a real LLM.
    """

    # @llm_behavior(model=None) resolves to this at registration / first run.
    default_model: str = "mock-chat-1"

    def __init__(self, *, note: Optional[str] = None) -> None:
        self._note = note or _DEFAULT_MOCK_NOTE

    def complete(
        self,
        *,
        system: str,
        messages: list,
        model: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        output_schema: Optional[type],
        timeout_seconds: float,
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> LLMResponse:
        text = self._note
        parsed: Any = None
        if output_schema is not None:
            try:
                parsed = output_schema(reply=text)
            except Exception:
                # Unknown schema shape — leave parsed=None; raw_text still set.
                parsed = None
        return LLMResponse(
            raw_text=json.dumps({"reply": text}),
            parsed=parsed,
            input_tokens=0,
            output_tokens=0,
            cost_usd=Decimal("0"),
            latency_seconds=0.0,
            model=model or self.default_model,
            finish_reason="stop",
        )

    def estimate_cost(
        self, *, input_tokens: int, output_tokens: int, model: str
    ) -> Decimal:
        return Decimal("0")

    def count_tokens(self, *, system: str, messages: list, model: str) -> int:
        return 0

    def recognizes_model(self, name: str) -> bool:
        # Permissive: the mock serves any model name.
        return True


# ================================================================ Provider compatibility
#
# The framework's canonical values are not always what a provider's wire API
# accepts. ProviderCompat absorbs those mismatches AT THE PROVIDER BOUNDARY —
# canonical names/params everywhere inside the graph and trace, provider-legal
# values on the wire, translated back on the way in. Nothing outside this
# seam ever sees a sanitized name.

# Canonical pack-scoped tool names are 'pack_name.tool_name'. OpenAI and
# Anthropic both reject '.' in function/tool names (^[a-zA-Z0-9_-]+$), so the
# dot becomes a double underscore on the wire and is reverse-mapped on the
# way back.
_SCOPED_NAME_SEP = "__"


def _safe_tool_name(name: str) -> str:
    return name.replace(".", _SCOPED_NAME_SEP)


class ProviderCompat:
    """Sanitize pack-scoped tool names at the provider boundary.

    Outbound: tool definitions and prior-turn assistant ``tool_calls`` get
    wire-legal names ('pack.tool' → 'pack__tool'). Inbound: tool calls in the
    response are mapped back to their canonical names, so the runtime's tool
    registry (which validates canonical names at registration) matches.

    A response tool name with no mapping (a hallucinated tool) passes through
    untouched — the runtime's own unknown-tool handling is the right place
    for that error, not a guess here.

    Wraps any ``LLMProvider``; a call with no ``tools`` is pure passthrough.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        # @llm_behavior(model=None) reads default_model off the provider.
        self.default_model = getattr(inner, "default_model", None)

    @staticmethod
    def _sanitize_message(message: Any) -> Any:
        """Return *message* with tool-call names sanitized (copy, not mutate —
        the runtime owns the message objects)."""
        calls = getattr(message, "tool_calls", None)
        if not calls:
            return message
        import dataclasses

        return dataclasses.replace(
            message,
            tool_calls=[
                dataclasses.replace(c, name=_safe_tool_name(c.name)) for c in calls
            ],
        )

    def complete(self, **kwargs: Any) -> LLMResponse:
        tools = kwargs.get("tools")
        if not tools:
            return self._inner.complete(**kwargs)

        name_map: dict[str, str] = {}  # wire-safe → canonical
        safe_tools = []
        for t in tools:
            t2 = dict(t)
            name = t2.get("name", "")
            safe = _safe_tool_name(name)
            if safe != name:
                name_map[safe] = name
                t2["name"] = safe
            safe_tools.append(t2)

        kwargs = dict(kwargs)
        kwargs["tools"] = safe_tools
        if name_map:
            kwargs["messages"] = [
                self._sanitize_message(m) for m in (kwargs.get("messages") or [])
            ]

        resp = self._inner.complete(**kwargs)
        if resp.tool_calls:
            resp.tool_calls = [
                ToolCall(id=c.id, name=name_map.get(c.name, c.name), args=c.args)
                for c in resp.tool_calls
            ]
        return resp

    def estimate_cost(self, **kwargs: Any) -> Decimal:
        return self._inner.estimate_cost(**kwargs)

    def count_tokens(self, **kwargs: Any) -> int:
        return self._inner.count_tokens(**kwargs)

    def recognizes_model(self, name: str) -> bool:
        return self._inner.recognizes_model(name)


# OpenAI's reasoning-model families reject `max_tokens` (they want
# `max_completion_tokens`) and non-default `temperature`/`top_p`. The
# framework provider sends all three unconditionally, so the translation
# happens one level down — on the OpenAI *client* payload.
_REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _is_reasoning_family(model: str) -> bool:
    return str(model).lower().startswith(_REASONING_MODEL_PREFIXES)


class _OpenAIParamShim:
    """Payload-rewriting facade over an OpenAI SDK client.

    Exposes just the ``chat.completions.create`` surface OpenAIProvider
    uses. For reasoning-family models: ``max_tokens`` →
    ``max_completion_tokens``, and ``temperature``/``top_p`` are dropped
    (those models accept only their defaults). Other models pass through
    byte-identical.
    """

    def __init__(self, inner_client: Any) -> None:
        self._inner = inner_client

        import types as _types

        self.chat = _types.SimpleNamespace(
            completions=_types.SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs: Any) -> Any:
        if _is_reasoning_family(kwargs.get("model", "")):
            kwargs = dict(kwargs)
            if "max_tokens" in kwargs:
                kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
            kwargs.pop("temperature", None)
            kwargs.pop("top_p", None)
        return self._inner.chat.completions.create(**kwargs)


class OpenAICompatProvider(OpenAIProvider):
    """OpenAIProvider with the reasoning-family parameter shim applied.

    Overrides the lazy ``_client()`` seam so the SDK import, env-var check,
    and client caching all stay exactly as the base provider implements
    them — the shim only wraps the returned client.
    """

    def _client(self) -> Any:
        return _OpenAIParamShim(super()._client())


def _make_native_provider(provider: str, model: Optional[str]):
    """Construct a native provider, pinning ``model`` as its default_model.

    chat_llm_responder is declared with ``model=None`` so the runtime resolves
    the model from the provider's ``default_model`` at call time. Setting it on
    the instance lets users choose a model (env / Secrets page) without the
    behavior pinning a provider-specific name at import time.
    """
    if provider == "openai":
        inst: Any = OpenAICompatProvider()
    elif provider == "anthropic":
        inst = AnthropicProvider()
    else:  # pragma: no cover - guarded by callers
        raise ValueError(f"no native provider for {provider!r}")
    if model:
        inst.default_model = model
    elif provider in _DEFAULT_MODELS:
        inst.default_model = _DEFAULT_MODELS[provider]
    return inst


class FallbackChatProvider:
    """Wrap a live provider so a plain chat completion failure degrades to an
    instructive mock reply instead of failing the chat turn with no message.

    A provider/network/auth error must never leave the user without an
    assistant reply — the fallback text names the actual error and explains
    how to fix the configuration. This wrapper preserves the native LLM
    lifecycle (it is itself an ``LLMProvider``) and only intercepts
    exceptions from the inner provider's ``complete``.

    EXCEPTION: calls with ``tools`` re-raise instead of degrading. A canned
    reply cannot participate in a tool loop, and swallowing the error there
    would corrupt the loop silently — the runtime's llm-error events are the
    correct surface for tool-loop failures.
    """

    def __init__(self, inner: Any, *, provider: str, key_env: Optional[str]) -> None:
        self._inner = inner
        # @llm_behavior(model=None) reads default_model off the provider.
        self.default_model = getattr(inner, "default_model", None)
        self._provider = provider
        self._key_env = key_env

    def complete(self, **kwargs: Any) -> LLMResponse:
        try:
            return self._inner.complete(**kwargs)
        except Exception as exc:
            # A tool loop cannot be served by canned text — the runtime is
            # waiting on tool calls or a grounded answer, and a mock reply
            # would silently corrupt the loop. Fail loud; the runtime's
            # llm-error handling owns that path.
            if kwargs.get("tools"):
                raise
            note = _live_error_note(self._provider, self._key_env, exc)
            return MockChatProvider(note=note).complete(**kwargs)

    def estimate_cost(self, **kwargs: Any) -> Decimal:
        try:
            return self._inner.estimate_cost(**kwargs)
        except Exception:
            return Decimal("0")

    def count_tokens(self, **kwargs: Any) -> int:
        try:
            return self._inner.count_tokens(**kwargs)
        except Exception:
            return 0

    def recognizes_model(self, name: str) -> bool:
        try:
            return bool(self._inner.recognizes_model(name))
        except Exception:
            return True


def resolve_chat_config(
    *,
    provider_pref: Optional[str] = None,
    model: Optional[str] = None,
) -> dict[str, Any]:
    """Resolve the effective chat LLM configuration from args + environment.

    Precedence: explicit args > ``CHAT_LLM_PROVIDER`` / ``CHAT_LLM_MODEL`` env
    > auto-detection from whichever provider key is present in the environment.

    Returns an info dict (never the secret value):
      mode:        "live" | "mock"
      provider:    resolved provider id ("openai" | "anthropic" | "mock")
      model:       effective model name (None in mock mode)
      key_env:     env var the key is read from (None in mock mode)
      key_present: whether that env var is set
    """
    pref = (provider_pref or os.environ.get("CHAT_LLM_PROVIDER") or "").strip().lower()
    model = (model or os.environ.get("CHAT_LLM_MODEL") or "").strip() or None

    chosen: Optional[str] = None
    if pref in _PROVIDER_KEY_ENV:
        if os.environ.get(_PROVIDER_KEY_ENV[pref]):
            chosen = pref
    if chosen is None and pref in ("", "auto", "mock"):
        for p in _AUTODETECT_ORDER:
            if os.environ.get(_PROVIDER_KEY_ENV[p]):
                chosen = p
                break

    if chosen is None:
        return {
            "mode": "mock",
            "provider": "mock",
            "model": None,
            "key_env": _PROVIDER_KEY_ENV.get(pref),
            "key_present": False,
            "requested_provider": pref or "auto",
        }

    key_env = _PROVIDER_KEY_ENV[chosen]
    eff_model = model or _DEFAULT_MODELS.get(chosen, "gpt-4o-mini")
    return {
        "mode": "live",
        "provider": chosen,
        "model": eff_model,
        "key_env": key_env,
        "key_present": True,
        "requested_provider": pref or "auto",
    }


def select_chat_provider(
    *,
    provider_pref: Optional[str] = None,
    model: Optional[str] = None,
) -> tuple[Any, dict[str, Any]]:
    """Return ``(provider_instance, info)`` for the resolved chat config.

    ``info`` is the dict from :func:`resolve_chat_config`. In live mode the
    native provider is wrapped in :class:`FallbackChatProvider` so a provider
    error degrades to an instructive mock reply rather than an empty turn; in
    mock mode the provider is a plain :class:`MockChatProvider`.
    """
    info = resolve_chat_config(provider_pref=provider_pref, model=model)
    if info["mode"] == "live":
        native = _make_native_provider(info["provider"], info["model"])
        # Order matters: Fallback(Compat(native)). ProviderCompat translates
        # names at the wire boundary; FallbackChatProvider sits outermost so
        # any failure (including inside the compat layer) still degrades to
        # an instructive reply on plain chat turns.
        provider: Any = FallbackChatProvider(
            ProviderCompat(native), provider=info["provider"], key_env=info["key_env"]
        )
    else:
        provider = MockChatProvider()
    return provider, info
