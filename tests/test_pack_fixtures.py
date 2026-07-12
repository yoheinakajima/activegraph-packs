"""pytest wrapper for all per-pack fixture suites.

Each parametrized case runs the corresponding pack's
``packs/<name>/fixtures/run_fixtures.py`` as a subprocess and asserts
exit code 0.  The fixture scripts themselves contain all assertion logic;
this file is intentionally thin — its only job is to make them
pytest-discoverable.

To add tests for a new pack:
    1. Add a ``fixtures/run_fixtures.py`` to your pack directory.
    2. Add its registered name and path to the PACKS list below.
"""

from __future__ import annotations

import pytest

from conftest import assert_fixture_passed, run_fixture_script

PACKS = [
    ("activity_normalizer", "activity_normalizer"),
    ("usage", "usage"),
    ("skills", "skills"),
    ("eval_outcome", "eval_outcome"),
    ("agent_profile", "agent_profile"),
    ("attention", "attention"),
    ("bridges", "bridges"),
    ("chat", "chat"),
    ("codebase", "codebase"),
    ("communication", "communication"),
    ("connector_control", "connector_control"),
    ("core", "core"),
    ("email", "email"),
    ("entity", "entity"),
    ("evolution", "evolution"),
    ("identity_auth", "identity_auth"),
    ("importer_chatgpt_export", "importers/chatgpt_export"),
    ("importer_claude_export", "importers/claude_export"),
    ("importer_assistant_self_summary", "importers/assistant_self_summary"),
    ("importer_assistant_local_sessions", "importers/assistant_local_sessions"),
    ("importer_public_presence", "importers/public_presence"),
    ("importer_local_files", "importers/local_files"),
    ("semantic_extraction", "semantic_extraction"),
    ("mcp", "mcp"),
    ("composio", "composio"),
    ("gmail", "gmail"),
    ("meeting", "meeting"),
    ("memory_gateway", "memory_gateway"),
    ("research", "research"),
    ("secrets", "secrets"),
    ("team_ops", "team_ops"),
    ("tool_gateway", "tool_gateway"),
]


@pytest.mark.parametrize(
    ("pack_name", "relative_path"), PACKS, ids=[name for name, _ in PACKS]
)
def test_pack_fixtures(pack_name: str, relative_path: str) -> None:
    """Run a pack's declared conventional fixture path and assert it passes."""
    result = run_fixture_script(f"packs/{relative_path}/fixtures/run_fixtures.py")
    assert_fixture_passed(result)
