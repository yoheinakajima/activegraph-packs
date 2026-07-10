"""ActiveGraph Pack Library.

Top-level package for all packs in this repository.
Import individual packs directly:

    from packs.core import pack as core_pack, CoreSettings
    from packs.tool_gateway import pack as tool_gateway_pack

Or use a bundle:

    from bundles.assistant import ASSISTANT_BUNDLE, build_assistant

Importing this package runs a runtime-version preflight: a pre-v1.9
activegraph raises a clear RuntimePreflightError here instead of a
baffling ``CapabilityDecl.__init__() got an unexpected keyword argument
'action_class'`` somewhere mid-import. See packs/_preflight.py.
"""

from packs._preflight import (
    RuntimePreflightError,
    assert_runtime_compatible,
)

assert_runtime_compatible()

__all__ = ["RuntimePreflightError", "assert_runtime_compatible"]
