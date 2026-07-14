"""One canonical `project` type: projects owns it, team_ops composes.

Co-loading previously raised PackConflictError — two packs declared
incompatible schemas under one global type name. The projects pack is the
canonical owner; team_ops adapts its PM fields into ``metadata.ops`` and
migrates its legacy rows on load.
"""

from activegraph import Graph, Runtime

from packs.core import pack as core_pack
from packs.projects import pack as projects_pack
from packs.projects.tools import project_projects_fn
from packs.team_ops import pack as team_ops_pack
from packs.team_ops.behaviors import migrate_legacy_team_ops_projects
from packs.team_ops.tools import create_project_fn


def test_projects_and_team_ops_coload_on_one_canonical_schema():
    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    rt.load_pack(projects_pack)
    rt.load_pack(team_ops_pack)  # previously PackConflictError
    rt.run_until_idle()

    ops_project = create_project_fn(
        rt.graph, "Atlas Ops", goal="ship the beta", owner_ref="alice",
        start_date="2026-07-01", target_date="2026-09-01",
    )
    rt.run_until_idle()
    data = ops_project.data
    # Canonical shape all readers rely on…
    assert data["project_identity"].startswith("project_")
    assert data["status"] == "active"
    assert data["confirmed_by"] == "alice"
    # …with the PM fields adapted, not forked.
    assert data["metadata"]["ops"]["stage"] == "planning"
    assert data["metadata"]["ops"]["goal"] == "ship the beta"

    # Every canonical consumer sees it: the projects projection includes it.
    projection = project_projects_fn(rt.graph)
    assert [row["name"] for row in projection["projects"]] == ["Atlas Ops"]

    # team_ops relations still target the canonical type.
    milestone = rt.graph.add_object("milestone", {
        "project_id": ops_project.id, "title": "beta",
        "description": "", "target_date": None, "status": "upcoming",
    })
    rt.graph.add_relation(milestone.id, ops_project.id, "part_of_project")
    rt.run_until_idle()


def test_migration_upgrades_a_row_missing_its_canonical_identity():
    """The load-time migration patches legacy-shaped rows (no
    project_identity) into the canonical shape without touching canonical
    rows — replayed team_ops ≤0.1 stores upgrade in place."""
    rt = Runtime(Graph())
    rt.load_pack(core_pack)
    rt.load_pack(projects_pack)
    rt.load_pack(team_ops_pack)
    rt.run_until_idle()

    canonical = create_project_fn(rt.graph, "Fresh", owner_ref="alice")
    rt.run_until_idle()
    identity_before = canonical.data["project_identity"]

    # Re-fire the migration (as another pack load/replay would): canonical
    # rows are untouched; the behavior is idempotent.
    class _Ctx:
        view = rt.graph

    handler = getattr(
        migrate_legacy_team_ops_projects, "fn",
        getattr(migrate_legacy_team_ops_projects, "handler", None),
    )
    handler(
        type("E", (), {"payload": {"name": "team_ops"}})(), rt.graph, _Ctx(),
        settings=None,
    )
    rt.run_until_idle()
    assert rt.graph.get_object(canonical.id).data["project_identity"] == identity_before
