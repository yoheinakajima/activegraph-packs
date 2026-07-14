"""Packs-level LLM provider configuration (D025 stage two).

The one place packs-side code learns which LLM provider is configured.
The runtime already ships ``AnthropicProvider`` and ``OpenAIProvider``;
this module only decides *which* to construct and *why* — it never
reimplements a client and it never touches key material. Providers read
their key from the environment lazily at first call; everything here
handles env var *names* only, so no key can appear in logs, events,
errors, or doctor output.

Resolution order (explicit settings first, environment fallback):

  1. ``LLMProviderSettings.provider`` — an explicit setting always wins,
     including on conflict with the environment.
  2. Environment fallback: ``ANTHROPIC_API_KEY`` if present, else
     ``OPENAI_API_KEY``. When both are present with no explicit setting,
     Anthropic wins (documented tie-break; set ``provider`` explicitly
     to override).
  3. Neither → no provider: the deterministic extraction floor is the
     supported zero-key mode (D009/D025), not an error.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Optional

from pydantic import BaseModel, Field

ProviderKind = Literal["anthropic", "openai"]

#: Which env var carries each provider's key. Names only — the values
#: are read lazily by the runtime providers, never by this module.
PROVIDER_KEY_ENVS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

#: Env-fallback probe order; also the tie-break when both keys exist.
_ENV_FALLBACK_ORDER: tuple[ProviderKind, ...] = ("anthropic", "openai")


class LLMProviderSettings(BaseModel):
    """The explicit-setting half of the resolution order.

    Hosts and bundles pass this once at startup (``configure_llm_provider``).
    Everything is optional: the empty settings object means "environment
    fallback decides".
    """

    provider: Optional[ProviderKind] = Field(
        default=None,
        description="Explicit provider selection; wins over the environment.",
    )
    model: Optional[str] = Field(
        default=None,
        description="Model override; None uses the provider's default_model.",
    )
    api_key_env: Optional[str] = Field(
        default=None,
        description=(
            "Override the env var NAME the provider reads its key from. "
            "The key value itself never passes through packs code."
        ),
    )
    # Logical model roles (ADR 0045 §5 as amended by ADR 0047 §6):
    # configuration, never hardcoded call sites. reasoning/coordinator serves
    # strategy, tool choice, ambiguity, and cross-source synthesis; balanced
    # serves aggregation, alignment, and contradiction/gap detection; fast
    # serves high-volume leaf reduction and distillation. None falls back per
    # ``resolve_model_for_role``.
    reasoning_model: Optional[str] = Field(
        default=None,
        description="Model for the reasoning/coordinator role.",
    )
    balanced_model: Optional[str] = Field(
        default=None,
        description="Model for the balanced aggregation/alignment role.",
    )
    fast_model: Optional[str] = Field(
        default=None,
        description="Model for the fast high-volume reduction role.",
    )
    # Pre-0047 role fields: still honored when the canonical field is unset,
    # so existing hosts and stores keep their configuration.
    comprehension_fast_model: Optional[str] = Field(
        default=None,
        description="Legacy alias for fast_model (batched leaf reductions).",
    )
    comprehension_reasoning_model: Optional[str] = Field(
        default=None,
        description="Legacy alias for reasoning_model (synthesis passes).",
    )


@dataclass(frozen=True)
class ResolvedLLMProvider:
    """The resolution verdict: which provider, and from where.

    ``api_key_env`` is the env var name the provider will read — safe to
    log and to print from doctor. ``provider`` is None in the zero-key
    mode (deterministic floor only).
    """

    provider: Optional[ProviderKind]
    source: Literal["setting", "env", "none"]
    api_key_env: Optional[str]
    model: Optional[str]

    @property
    def configured(self) -> bool:
        return self.provider is not None


def resolve_llm_provider(
    settings: Optional[LLMProviderSettings] = None,
    env: Optional[Mapping[str, str]] = None,
) -> ResolvedLLMProvider:
    """Apply the resolution order. Pure over (settings, env) — no I/O."""
    environ = os.environ if env is None else env
    settings = settings or LLMProviderSettings()

    if settings.provider is not None:
        return ResolvedLLMProvider(
            provider=settings.provider,
            source="setting",
            api_key_env=settings.api_key_env
            or PROVIDER_KEY_ENVS[settings.provider],
            model=settings.model,
        )

    for kind in _ENV_FALLBACK_ORDER:
        if environ.get(PROVIDER_KEY_ENVS[kind]):
            return ResolvedLLMProvider(
                provider=kind,
                source="env",
                api_key_env=PROVIDER_KEY_ENVS[kind],
                model=settings.model,
            )

    return ResolvedLLMProvider(
        provider=None, source="none", api_key_env=None, model=None
    )


def build_llm_provider(resolved: ResolvedLLMProvider):
    """Construct the runtime provider for a resolved configuration.

    Returns None in zero-key mode. Construction is lazy on the provider
    side: the key env var is read at first call, not here, and a missing
    key raises naming only the env var.
    """
    if resolved.provider is None:
        return None
    if resolved.provider == "anthropic":
        from activegraph.llm import AnthropicProvider

        return AnthropicProvider(api_key_env=resolved.api_key_env)
    from activegraph.llm import OpenAIProvider

    return OpenAIProvider(api_key_env=resolved.api_key_env)


def default_model_for(resolved: ResolvedLLMProvider) -> Optional[str]:
    """The model extraction should use: explicit override or the
    provider's own default_model."""
    if resolved.provider is None:
        return None
    if resolved.model:
        return resolved.model
    provider = build_llm_provider(resolved)
    return getattr(provider, "default_model", None)


#: Known-good tier defaults per provider. Model names here are configuration
#: defaults, never doctrine (ADR 0047 §6): an explicit role setting always
#: wins, and providers without a listed tier fall back honestly.
FAST_MODEL_DEFAULTS: dict[str, str] = {
    "anthropic": "claude-haiku-4-5",
}
BALANCED_MODEL_DEFAULTS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-5",
}

#: The three logical roles (ADR 0047 §6). Legacy names keep resolving so
#: pre-0047 call sites and persisted stores stay valid.
MODEL_ROLES = ("reasoning", "balanced", "fast")
LEGACY_MODEL_ROLES = {
    "comprehension_fast": "fast",
    "comprehension_reasoning": "reasoning",
}

#: Settings-field fallback order per canonical role: the first configured
#: field wins (canonical field first, then its legacy alias).
_ROLE_SETTING_FIELDS = {
    "reasoning": ("reasoning_model", "comprehension_reasoning_model"),
    "balanced": ("balanced_model",),
    "fast": ("fast_model", "comprehension_fast_model"),
}


def canonical_model_role(role: str) -> str:
    """Map a role name (canonical or legacy) to its canonical role."""
    canonical = LEGACY_MODEL_ROLES.get(role, role)
    if canonical not in MODEL_ROLES:
        raise ValueError(
            f"unknown model role {role!r}; expected one of {MODEL_ROLES} "
            f"(legacy: {tuple(LEGACY_MODEL_ROLES)})"
        )
    return canonical


def resolve_role(
    role: str,
    resolved: Optional[ResolvedLLMProvider] = None,
    settings: Optional[LLMProviderSettings] = None,
) -> dict[str, Any]:
    """Resolve a logical role to its provider/model verdict (ADR 0047 §6).

    Returns ``{"role", "provider", "model", "source", "aliased_to"}`` —
    the record every call site should attach to its run/receipt so which
    model actually served a role stays inspectable. ``aliased_to`` names the
    tier a balanced role borrowed when the provider has no middle default.
    Zero-key resolves to ``model=None`` (the deterministic floor).
    """
    canonical = canonical_model_role(role)
    if resolved is None:
        resolved = configured_llm_provider()
    verdict: dict[str, Any] = {
        "role": canonical,
        "provider": None,
        "model": None,
        "source": "none",
        "aliased_to": None,
    }
    if resolved is None or resolved.provider is None:
        return verdict
    verdict["provider"] = resolved.provider
    settings = settings or _CONFIGURED_SETTINGS or LLMProviderSettings()
    for field in _ROLE_SETTING_FIELDS[canonical]:
        configured = getattr(settings, field, None)
        if configured:
            verdict["model"] = str(configured)
            verdict["source"] = f"setting:{field}"
            return verdict
    tier_defaults = {
        "fast": FAST_MODEL_DEFAULTS,
        "balanced": BALANCED_MODEL_DEFAULTS,
        "reasoning": {},
    }[canonical]
    tier_model = tier_defaults.get(resolved.provider)
    if tier_model:
        verdict["model"] = tier_model
        verdict["source"] = "tier_default"
        return verdict
    if canonical == "balanced":
        # No configured or known middle tier: balanced may alias the
        # reasoning tier, recorded explicitly (ADR 0047 §6).
        reasoning = resolve_role("reasoning", resolved, settings)
        verdict["model"] = reasoning["model"]
        verdict["source"] = reasoning["source"]
        verdict["aliased_to"] = "reasoning"
        return verdict
    verdict["model"] = default_model_for(resolved)
    verdict["source"] = "provider_default"
    return verdict


def resolve_model_for_role(
    role: str,
    resolved: Optional[ResolvedLLMProvider] = None,
    settings: Optional[LLMProviderSettings] = None,
) -> Optional[str]:
    """The model id for a logical role — thin accessor over ``resolve_role``."""
    return resolve_role(role, resolved, settings)["model"]


# ------------------------------------------------------- process registry
#
# The configured provider for this process, mirroring the local-capability
# registry pattern (set once at startup, cleared between test fixtures).

_CONFIGURED: Optional[ResolvedLLMProvider] = None
_PROVIDER_INSTANCE = None
_CONFIGURED_SETTINGS: Optional[LLMProviderSettings] = None


def configure_llm_provider(
    settings: Optional[LLMProviderSettings] = None,
    env: Optional[Mapping[str, str]] = None,
) -> ResolvedLLMProvider:
    """Resolve and store this process's LLM provider configuration."""
    global _CONFIGURED, _PROVIDER_INSTANCE, _CONFIGURED_SETTINGS
    _CONFIGURED = resolve_llm_provider(settings, env)
    _PROVIDER_INSTANCE = build_llm_provider(_CONFIGURED)
    _CONFIGURED_SETTINGS = settings
    return _CONFIGURED


def configured_llm_provider() -> ResolvedLLMProvider:
    """The stored resolution; resolves from the environment on first use
    so zero-configuration hosts still get the env fallback."""
    global _CONFIGURED, _PROVIDER_INSTANCE
    if _CONFIGURED is None:
        _CONFIGURED = resolve_llm_provider()
        _PROVIDER_INSTANCE = build_llm_provider(_CONFIGURED)
    return _CONFIGURED


def get_llm_provider():
    """The constructed runtime provider instance, or None (zero-key)."""
    configured_llm_provider()
    return _PROVIDER_INSTANCE


def set_llm_provider(provider, resolved: ResolvedLLMProvider) -> None:
    """Install an already-built provider (tests: recorded providers)."""
    global _CONFIGURED, _PROVIDER_INSTANCE
    _CONFIGURED = resolved
    _PROVIDER_INSTANCE = provider


def clear_llm_provider() -> None:
    """Reset the registry — call between test fixtures."""
    global _CONFIGURED, _PROVIDER_INSTANCE, _CONFIGURED_SETTINGS
    _CONFIGURED = None
    _PROVIDER_INSTANCE = None
    _CONFIGURED_SETTINGS = None


def response_text(response) -> str:
    """The text of a provider completion, whatever the provider shape.

    The runtime's LLMResponse carries ``raw_text``; hand-rolled and test
    providers often carry ``text``. Reading the wrong one is silent — the
    first live keyed run returned real model output that every consumer
    read as "" (empty draft, no_results research) because call sites used
    ``getattr(response, "text", "")`` against LLMResponse. One seam, both
    shapes, never silently empty again.
    """
    for attr in ("raw_text", "text"):
        value = getattr(response, attr, None)
        if isinstance(value, str) and value:
            return value
    return ""


def response_finish_reason(response) -> str:
    """The provider finish/stop reason when the shape carries one."""
    value = getattr(response, "finish_reason", None)
    return value if isinstance(value, str) else ""


def parse_json_payload(text: str) -> Optional[dict]:
    """Best-effort extraction of one JSON object from a model response.

    Providers wrap JSON in prose or code fences, and a strict first-brace
    regex silently yields nothing (live bug: research "found nothing" with
    no error). Order: the whole text, then fenced blocks, then balanced
    top-level objects. Returns the first dict that parses, else None —
    callers keep their own schema validation.
    """
    import json
    import re

    candidate = (text or "").strip()
    if not candidate:
        return None
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", candidate, re.DOTALL):
        try:
            parsed = json.loads(match.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    depth, start = 0, None
    for index, char in enumerate(candidate):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    parsed = json.loads(candidate[start:index + 1])
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    start = None
    return None


__all__ = [
    "BALANCED_MODEL_DEFAULTS",
    "FAST_MODEL_DEFAULTS",
    "LEGACY_MODEL_ROLES",
    "MODEL_ROLES",
    "PROVIDER_KEY_ENVS",
    "canonical_model_role",
    "resolve_model_for_role",
    "resolve_role",
    "response_text",
    "response_finish_reason",
    "LLMProviderSettings",
    "ResolvedLLMProvider",
    "build_llm_provider",
    "clear_llm_provider",
    "configure_llm_provider",
    "configured_llm_provider",
    "default_model_for",
    "get_llm_provider",
    "parse_json_payload",
    "resolve_llm_provider",
    "set_llm_provider",
]
