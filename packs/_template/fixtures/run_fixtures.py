"""Run <your pack> fixture scenarios.

Every pack ships this file: deterministic, runnable scenarios proving the
pack's behavior surface with no network, no API keys, and no wall-clock
dependence. CI runs it (add your pack to tests/test_pack_fixtures.py and
a step in .github/workflows/ci.yml); the pack manifest's
`deterministic = true` assertion refers to exactly this property.

Usage:
    python packs/<your_pack>/fixtures/run_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parents[2]))

from activegraph import Graph, Runtime

from packs.core import pack as core_pack

# from packs.<your_pack> import pack as your_pack, YourSettings


def run_example_fixture() -> dict:
    """One scenario: seed input objects, run to idle, assert the cascade."""
    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    # rt.load_pack(your_pack, settings=YourSettings())

    # rt.graph.add_object("source", {...})
    rt.run_until_idle()

    # assert <expected objects exist>, "explain what broke"
    return {"ok": True}


def run_all() -> bool:
    print("=" * 60)
    print("<Your Pack> Fixtures")
    print("=" * 60)

    print("\n[1] example scenario")
    result = run_example_fixture()
    print(f"  PASS: {result}")

    print("\nALL PASS")
    return True


if __name__ == "__main__":
    try:
        ok = run_all()
    except AssertionError as exc:
        print(f"\nFAIL: {exc}")
        sys.exit(1)
    sys.exit(0 if ok else 1)
