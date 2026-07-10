"""Runtime-version preflight for the pack library (H2).

Why this exists: importing these packs against a pre-v1.9 runtime dies
mid-import with ``CapabilityDecl.__init__() got an unexpected keyword
argument 'action_class'`` — a fail-weird that says nothing about the
actual problem (the installed runtime is older than this pack library's
contract). Worse, ``pip`` can *silently* leave a stale runtime in place
when it cannot fetch a git pin, so "I just installed it" proves nothing.
This module turns that incident class into one loud, actionable error at
``import packs`` time.

What decides pass/fail: **feature detection**, not the version string.
``activegraph.__version__`` lies for editable installs — the runtime's
main carried the v1.9 contract while still reporting ``1.7.1`` — so a
runtime whose ``CapabilityDecl`` already accepts ``action_class`` passes
even if its version string lags. A runtime missing the field fails
regardless of what its version string claims (the string may also lie
*new*). The version string's job is to make the error message concrete.

The doctor (``python -m packs.doctor``) surfaces the same probe
diagnostically; keep the two in sync by keeping the logic here.
"""

from __future__ import annotations

import dataclasses
from types import ModuleType

#: Minimum runtime the pack library's contract requires (CONTRACT v1.9 —
#: canonical action_class authority). Bump together with the pyproject pin.
REQUIRED_FLOOR = "1.9"

#: The v1.9 feature the packs actually import against: ``CapabilityDecl``
#: grew the ``action_class`` field (CONTRACT v1.9 #1).
_FLOOR_FEATURE = "action_class"


class RuntimePreflightError(ImportError):
    """The installed activegraph runtime predates this pack library's
    contract. Raised at ``import packs`` so the failure names the fix
    instead of surfacing as an unrelated ``TypeError`` mid-import."""


def preflight_message(found_version: str) -> str:
    """The one error message for a too-old runtime, verbatim per H2."""
    return (
        f"activegraph >= {REQUIRED_FLOOR} is required (found {found_version}). "
        f"The PyPI release is older than\n"
        f"this pack library's contract. Install from source until v1.9 publishes:\n"
        f'  pip install -e "./activegraph-src[llm]"   (clone as activegraph-src — a dir\n'
        f"  named activegraph would shadow the package). See README quickstart."
    )


def runtime_supports_contract(runtime_module: ModuleType | None = None) -> bool:
    """True when the installed runtime carries the v1.9 contract floor.

    Feature-detects ``CapabilityDecl.action_class`` rather than trusting
    ``__version__`` (editable installs lie in both directions). Accepts
    an explicit module for tests; defaults to the installed runtime.
    """
    try:
        if runtime_module is None:
            from activegraph.packs import manifest  # noqa: PLC0415 — import-time probe
        else:
            manifest = runtime_module.packs.manifest
        decl = manifest.CapabilityDecl
        fields = {f.name for f in dataclasses.fields(decl)}
    except (ImportError, AttributeError, TypeError):
        # No CapabilityDecl at all (or not a dataclass): far older than
        # the floor — same incident, same answer.
        return False
    return _FLOOR_FEATURE in fields


def found_runtime_version(runtime_module: ModuleType | None = None) -> str:
    """The installed runtime's self-reported version, for messages only."""
    if runtime_module is None:
        import activegraph as runtime_module  # noqa: PLC0415
    return str(getattr(runtime_module, "__version__", "unknown"))


def assert_runtime_compatible(runtime_module: ModuleType | None = None) -> None:
    """Raise :class:`RuntimePreflightError` if the runtime predates the
    contract floor; return silently otherwise. Called once at
    ``import packs``."""
    if not runtime_supports_contract(runtime_module):
        raise RuntimePreflightError(
            preflight_message(found_runtime_version(runtime_module))
        )
