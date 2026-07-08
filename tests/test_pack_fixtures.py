"""pytest wrapper for all 16 per-pack fixture suites.

Each parametrized case runs the corresponding pack's
``packs/<name>/fixtures/run_fixtures.py`` as a subprocess and asserts
exit code 0.  The fixture scripts themselves contain all assertion logic;
this file is intentionally thin — its only job is to make them
pytest-discoverable.

To add tests for a new pack:
    1. Add a ``fixtures/run_fixtures.py`` to your pack directory.
    2. Add the pack name to the PACKS list below.
"""

from __future__ import annotations

import pytest

from conftest import assert_fixture_passed, run_fixture_script

PACKS = [
    "agent_profile",
    "bridges",
    "chat",
    "codebase",
    "communication",
    "core",
    "email",
    "entity",
    "identity_auth",
    "mcp",
    "meeting",
    "memory_gateway",
    "research",
    "secrets",
    "team_ops",
    "tool_gateway",
    "vc",
]


@pytest.mark.parametrize("pack_name", PACKS)
def test_pack_fixtures(pack_name: str) -> None:
    """Run ``packs/<pack_name>/fixtures/run_fixtures.py`` and assert it passes."""
    result = run_fixture_script(f"packs/{pack_name}/fixtures/run_fixtures.py")
    assert_fixture_passed(result)
