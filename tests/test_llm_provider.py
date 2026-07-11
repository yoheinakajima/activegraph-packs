"""Packs-level LLM provider configuration (D025 stage two, Part 1).

The resolution order, the doctor `llm-provider` check in all three
states, and the property the whole part exists for: key material never
appears in logs, errors, or doctor output.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import pytest

from packs.doctor import FAIL, PASS, check_llm_provider, main, run_doctor
from packs.llm_provider import (
    LLMProviderSettings,
    build_llm_provider,
    clear_llm_provider,
    configure_llm_provider,
    get_llm_provider,
    resolve_llm_provider,
)

FAKE_ANTHROPIC_KEY = "sk-ant-FAKE-key-material-9f8e7d6c5b4a"
FAKE_OPENAI_KEY = "sk-proj-FAKE-key-material-1a2b3c4d5e6f"


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_llm_provider()
    yield
    clear_llm_provider()


# ------------------------------------------------------- resolution order


def test_no_setting_no_env_resolves_to_none():
    resolved = resolve_llm_provider(env={})
    assert resolved.provider is None
    assert resolved.source == "none"
    assert not resolved.configured
    assert build_llm_provider(resolved) is None


def test_env_fallback_selects_by_which_key_is_present():
    anthropic = resolve_llm_provider(env={"ANTHROPIC_API_KEY": FAKE_ANTHROPIC_KEY})
    assert (anthropic.provider, anthropic.source) == ("anthropic", "env")
    assert anthropic.api_key_env == "ANTHROPIC_API_KEY"

    openai = resolve_llm_provider(env={"OPENAI_API_KEY": FAKE_OPENAI_KEY})
    assert (openai.provider, openai.source) == ("openai", "env")
    assert openai.api_key_env == "OPENAI_API_KEY"


def test_both_env_keys_without_setting_break_toward_anthropic():
    resolved = resolve_llm_provider(
        env={
            "ANTHROPIC_API_KEY": FAKE_ANTHROPIC_KEY,
            "OPENAI_API_KEY": FAKE_OPENAI_KEY,
        }
    )
    assert resolved.provider == "anthropic"
    assert resolved.source == "env"


def test_explicit_setting_wins_on_conflict_with_env():
    """Both keys present AND the environment favors the other provider:
    the explicit setting still decides."""
    resolved = resolve_llm_provider(
        LLMProviderSettings(provider="openai"),
        env={
            "ANTHROPIC_API_KEY": FAKE_ANTHROPIC_KEY,
            "OPENAI_API_KEY": FAKE_OPENAI_KEY,
        },
    )
    assert (resolved.provider, resolved.source) == ("openai", "setting")
    assert resolved.api_key_env == "OPENAI_API_KEY"


def test_setting_can_override_model_and_key_env_name():
    resolved = resolve_llm_provider(
        LLMProviderSettings(
            provider="anthropic",
            model="claude-haiku-4-5",
            api_key_env="MY_ANTHROPIC_KEY",
        ),
        env={},
    )
    assert resolved.model == "claude-haiku-4-5"
    assert resolved.api_key_env == "MY_ANTHROPIC_KEY"


def test_build_constructs_runtime_providers_not_reimplementations():
    from activegraph.llm import AnthropicProvider, OpenAIProvider

    anthropic = build_llm_provider(
        resolve_llm_provider(env={"ANTHROPIC_API_KEY": FAKE_ANTHROPIC_KEY})
    )
    assert isinstance(anthropic, AnthropicProvider)
    openai = build_llm_provider(
        resolve_llm_provider(env={"OPENAI_API_KEY": FAKE_OPENAI_KEY})
    )
    assert isinstance(openai, OpenAIProvider)


def test_process_registry_configure_then_get():
    resolved = configure_llm_provider(
        LLMProviderSettings(provider="anthropic"), env={}
    )
    assert resolved.source == "setting"
    assert get_llm_provider() is not None

    clear_llm_provider()
    configure_llm_provider(env={})
    assert get_llm_provider() is None


# ------------------------------------------------- doctor: llm-provider


def test_doctor_none_state_is_a_pass_with_note_never_a_failure():
    result = check_llm_provider(settings=None, env={})
    assert result.status == PASS
    assert "none — deterministic floor only" in result.detail


def test_doctor_env_state_reports_provider_and_source():
    result = check_llm_provider(
        settings=None, env={"ANTHROPIC_API_KEY": FAKE_ANTHROPIC_KEY}
    )
    assert result.status == PASS
    assert "anthropic (from env, key env ANTHROPIC_API_KEY)" in result.detail


def test_doctor_setting_state_reports_provider_and_source():
    result = check_llm_provider(
        settings=LLMProviderSettings(provider="openai"),
        env={"OPENAI_API_KEY": FAKE_OPENAI_KEY},
    )
    assert result.status == PASS
    assert "openai (from setting, key env OPENAI_API_KEY)" in result.detail


def test_doctor_setting_without_its_key_fails_naming_only_the_env_var():
    result = check_llm_provider(
        settings=LLMProviderSettings(provider="anthropic"), env={}
    )
    assert result.status == FAIL
    assert "ANTHROPIC_API_KEY is not set" in result.detail


def test_doctor_makes_no_network_calls_by_default():
    calls: list[object] = []
    result = check_llm_provider(
        settings=None,
        env={"ANTHROPIC_API_KEY": FAKE_ANTHROPIC_KEY},
        live=False,
        live_ping=calls.append,
    )
    assert result.status == PASS
    assert calls == []
    assert "live ping" not in result.detail


def test_doctor_live_flag_does_one_minimal_ping():
    calls: list[object] = []
    ok = check_llm_provider(
        settings=None,
        env={"ANTHROPIC_API_KEY": FAKE_ANTHROPIC_KEY},
        live=True,
        live_ping=calls.append,
    )
    assert ok.status == PASS
    assert "live ping ok" in ok.detail
    assert len(calls) == 1

    def _boom(provider):
        raise RuntimeError("auth rejected")

    bad = check_llm_provider(
        settings=None,
        env={"ANTHROPIC_API_KEY": FAKE_ANTHROPIC_KEY},
        live=True,
        live_ping=_boom,
    )
    assert bad.status == FAIL
    assert "live ping failed" in bad.detail


def test_doctor_exit_zero_with_and_without_a_key(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main([]) == 0
    without_key = capsys.readouterr().out
    assert "llm-provider" in without_key
    assert "none — deterministic floor only" in without_key

    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_ANTHROPIC_KEY)
    assert main([]) == 0
    with_key = capsys.readouterr().out
    assert "anthropic (from env, key env ANTHROPIC_API_KEY)" in with_key


# ------------------------------------------------- the key never leaks


def test_key_material_never_appears_in_logs_errors_or_doctor_output(
    monkeypatch, capsys, caplog
):
    """Thread a fake key through every packs-side surface this part adds
    and grep the captured output for the key material."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with caplog.at_level(logging.DEBUG):
        resolved = configure_llm_provider()
        provider = get_llm_provider()

        # Every printable surface of the resolution verdict.
        surfaces = [repr(resolved), str(resolved), repr(provider)]

        # The doctor run end to end, text and JSON.
        assert main([]) == 0
        surfaces.append(capsys.readouterr().out)
        assert main(["--json"]) == 0
        surfaces.append(capsys.readouterr().out)

        # The provider's own missing-key error names the env var only.
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        fresh = build_llm_provider(resolved)
        with pytest.raises(RuntimeError) as excinfo:
            fresh._client()
        surfaces.append(str(excinfo.value))

    surfaces.append(caplog.text)
    for surface in surfaces:
        assert FAKE_ANTHROPIC_KEY not in surface
    assert "ANTHROPIC_API_KEY" in str(excinfo.value)
