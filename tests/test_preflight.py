"""Runtime-version preflight (H2).

Importing packs against a pre-v1.9 runtime used to die mid-import with
``CapabilityDecl.__init__() got an unexpected keyword argument
'action_class'``. The preflight replaces that fail-weird with one loud,
actionable error at ``import packs``. These tests pin three things:

  1. a stubbed old runtime produces exactly the promised message;
  2. feature detection is the decider — the version string may lie in
     either direction for editable installs;
  3. the current environment changes not at all (import succeeds, the
     assertion is a no-op — the rest of this suite is the regression).
"""

from __future__ import annotations

import dataclasses
from types import ModuleType, SimpleNamespace

import pytest

import packs
from packs._preflight import (
    RuntimePreflightError,
    assert_runtime_compatible,
    found_runtime_version,
    preflight_message,
    runtime_supports_contract,
)


@dataclasses.dataclass(frozen=True)
class _OldCapabilityDecl:
    """Pre-v1.9 shape: no action_class field."""

    provider: str
    capability: str
    risk_class: str
    credential_ref: str = ""


@dataclasses.dataclass(frozen=True)
class _NewCapabilityDecl:
    """v1.9 shape: action_class present (CONTRACT v1.9 #1)."""

    provider: str
    capability: str
    risk_class: str
    credential_ref: str = ""
    action_class: str = ""


def _stub_runtime(version: str, decl: type) -> ModuleType:
    stub = ModuleType("activegraph")
    stub.__version__ = version
    stub.packs = SimpleNamespace(
        manifest=SimpleNamespace(CapabilityDecl=decl)
    )
    return stub


def test_old_runtime_raises_the_exact_promised_message() -> None:
    """The 1.7.1-from-PyPI incident: version old, feature missing."""
    stub = _stub_runtime("1.7.1", _OldCapabilityDecl)
    with pytest.raises(RuntimePreflightError) as excinfo:
        assert_runtime_compatible(stub)
    assert str(excinfo.value) == (
        "activegraph >= 1.9 is required (found 1.7.1). "
        "The PyPI release is older than\n"
        "this pack library's contract. "
        "Install from source until v1.9 publishes:\n"
        '  pip install -e "./activegraph-src[llm]"   '
        "(clone as activegraph-src — a dir\n"
        "  named activegraph would shadow the package). "
        "See README quickstart."
    )


def test_lying_new_version_string_still_fails_on_missing_feature() -> None:
    """Backstop: pip left a stale tree but metadata/string claims 1.9."""
    stub = _stub_runtime("1.9.0", _OldCapabilityDecl)
    with pytest.raises(RuntimePreflightError) as excinfo:
        assert_runtime_compatible(stub)
    assert str(excinfo.value) == preflight_message("1.9.0")
    assert "(found 1.9.0)" in str(excinfo.value)


def test_lying_old_version_string_passes_when_feature_present() -> None:
    """An editable install of a post-v1.9 tree whose __version__ still
    says 1.7.1 (the runtime's own main sat in exactly this state) must
    NOT be blocked: the contract the packs import against is there."""
    stub = _stub_runtime("1.7.1", _NewCapabilityDecl)
    assert runtime_supports_contract(stub) is True
    assert assert_runtime_compatible(stub) is None


def test_runtime_missing_manifest_module_entirely_raises() -> None:
    """Far-pre-1.9 runtimes without CapabilityDecl get the same answer."""
    stub = ModuleType("activegraph")
    stub.__version__ = "0.9.0"
    with pytest.raises(RuntimePreflightError) as excinfo:
        assert_runtime_compatible(stub)
    assert str(excinfo.value) == preflight_message("0.9.0")


def test_runtime_without_version_attribute_reports_unknown() -> None:
    stub = ModuleType("activegraph")
    assert found_runtime_version(stub) == "unknown"
    with pytest.raises(RuntimePreflightError) as excinfo:
        assert_runtime_compatible(stub)
    assert "(found unknown)" in str(excinfo.value)


def test_current_environment_passes_and_import_ran_the_check() -> None:
    """Zero behavior change on the installed runtime: the probe passes,
    and ``import packs`` (already executed above) ran it at import."""
    assert runtime_supports_contract() is True
    assert assert_runtime_compatible() is None
    assert packs.RuntimePreflightError is RuntimePreflightError
