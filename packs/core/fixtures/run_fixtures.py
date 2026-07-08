"""Run Core Pack fixture scenarios and assert expected_outputs.

Reads each YAML fixture file from this directory, creates the declared
objects in a fresh Graph, calls rt.run_until_idle() so Core Pack
behaviors react, then asserts the expected_outputs section.

Usage:
    python packs/core/fixtures/run_fixtures.py

All scenarios run without an LLM or API key.
Exit code 0 if all assertions pass; exit code 1 if any fail.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running from repo root or from this directory
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parents[2]))

import yaml
from activegraph import Graph, Runtime
from packs.core import pack, CoreSettings


# ------------------------------------------------------------------ helpers


def _load_fixtures() -> list[tuple[str, dict]]:
    """Load all YAML fixture files from this directory."""
    files = sorted(_HERE.glob("*.yaml"))
    return [(f.stem, yaml.safe_load(f.read_text())) for f in files]


def _resolve_ref(ref: str, created_ids: dict[str, list[str]]) -> str | None:
    """Resolve a "type[index]" reference like "artifact[0]" to an object ID."""
    if "[" in ref:
        t, rest = ref.rstrip("]").split("[", 1)
        lst = created_ids.get(t, [])
        try:
            return lst[int(rest)]
        except (IndexError, ValueError):
            return None
    return None


def _run_fixture(name: str, scenario: dict) -> tuple[bool, list[str]]:
    """Run one fixture scenario and return (passed, list_of_failures)."""
    failures: list[str] = []

    graph = Graph()
    rt = Runtime(graph)
    rt.load_pack(pack, settings=CoreSettings())

    # Create all declared objects
    # Behaviors fire reactively as objects are added (Graph emits events
    # and Runtime dispatches them). We collect and call run_until_idle()
    # at the end to ensure all cascading behavior runs complete.
    created_ids: dict[str, list[str]] = {}

    for obj_spec in scenario.get("objects", []):
        obj_type = obj_spec["type"]
        obj_data = obj_spec["data"]

        obj = graph.add_object(obj_type, obj_data)
        created_ids.setdefault(obj_type, []).append(obj.id)

    # Let all reactive behaviors run to completion
    rt.run_until_idle()

    # Create any explicitly declared relations (after behaviors have run,
    # so behavior-created objects are available)
    for rel_spec in scenario.get("relations", []):
        rel_type = rel_spec["type"]
        src_id = _resolve_ref(rel_spec.get("source", ""), created_ids)
        tgt_id = _resolve_ref(rel_spec.get("target", ""), created_ids)
        if src_id and tgt_id:
            try:
                graph.add_relation(src_id, tgt_id, rel_type)
            except Exception as e:
                failures.append(f"  Failed to create relation {rel_type}: {e}")

    rt.run_until_idle()

    # Gather final graph state
    all_objects = list(graph.objects())
    by_type: dict[str, list] = {}
    for o in all_objects:
        by_type.setdefault(o.type, []).append(o)

    all_relations = list(graph.relations())

    # Relation fields are exactly what they say: r.source / r.target are
    # object IDs, r.type is the relation type name. (An earlier comment here
    # claimed otherwise — it was describing malformed relations produced by
    # calling add_relation with the arguments in the wrong order.)
    relation_types_in_graph = {r.type for r in all_relations}

    # ---- Assert expected_outputs ----
    expected = scenario.get("expected_outputs", {})

    # --- observations ---
    if "observations" in expected:
        exp = expected["observations"]
        actual_obs = by_type.get("observation", [])
        count = len(actual_obs)

        if "min_count" in exp and count < exp["min_count"]:
            failures.append(
                f"  observations: expected >= {exp['min_count']}, got {count}"
            )
        if "max_count" in exp and count > exp["max_count"]:
            failures.append(
                f"  observations: expected <= {exp['max_count']}, got {count}"
            )
        if "at_least_one_with" in exp:
            for field, value in exp["at_least_one_with"].items():
                matches = [o for o in actual_obs if o.data.get(field) == value]
                if not matches:
                    actual_vals = [o.data.get(field) for o in actual_obs]
                    failures.append(
                        f"  observations: expected at least one with {field}={value!r}; "
                        f"actual {field} values: {actual_vals}"
                    )

    # --- memory_candidates ---
    if "memory_candidates" in expected:
        exp = expected["memory_candidates"]
        actual_mc = by_type.get("memory_candidate", [])
        count = len(actual_mc)

        if "min_count" in exp and count < exp["min_count"]:
            failures.append(
                f"  memory_candidates: expected >= {exp['min_count']}, got {count}"
            )
        if "each_has" in exp:
            for field in exp["each_has"]:
                missing = [mc for mc in actual_mc if not mc.data.get(field)]
                if missing:
                    failures.append(
                        f"  memory_candidates: {len(missing)} object(s) missing field '{field}'"
                    )

    # --- artifacts ---
    if "artifacts" in expected:
        exp = expected["artifacts"]
        actual_art = by_type.get("artifact", [])
        if "count" in exp and len(actual_art) != exp["count"]:
            failures.append(
                f"  artifacts: expected {exp['count']}, got {len(actual_art)}"
            )

    # --- relations ---
    if "relations" in expected:
        for rel_spec in expected["relations"].get("includes", []):
            rtype = rel_spec["type"]
            if rtype not in relation_types_in_graph:
                failures.append(
                    f"  relations: expected '{rtype}' ({rel_spec.get('description', '')}), "
                    f"not found. Present relation types: {sorted(relation_types_in_graph)}"
                )

    return (len(failures) == 0), failures


# ------------------------------------------------------------------ main


def main():
    fixtures = _load_fixtures()
    if not fixtures:
        print("No YAML fixture files found in", _HERE)
        sys.exit(1)

    results: list[tuple[str, bool, list[str]]] = []

    for name, scenario in fixtures:
        print(f"\n{'='*60}")
        print(f"Fixture: {name}")
        desc = scenario.get("description", "").strip().replace("\n", " ")
        print(f"  {desc}")
        print(f"{'='*60}")

        passed, failures = _run_fixture(name, scenario)
        results.append((name, passed, failures))

        if passed:
            print("  PASS")
        else:
            print(f"  FAIL — {len(failures)} assertion(s) failed:")
            for msg in failures:
                print(msg)

    total = len(results)
    passed_count = sum(1 for _, ok, _ in results if ok)

    print(f"\n{'='*60}")
    print(f"Results: {passed_count}/{total} fixtures passed")
    print(f"{'='*60}\n")

    if passed_count < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
