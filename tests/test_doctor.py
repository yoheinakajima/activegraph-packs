"""``python -m packs.doctor`` (H4).

Each check fails correctly against a simulated broken environment and
passes on the healthy one this suite runs in. The broken environments
are injected (stub modules, fake entry points, unwritable tmp dirs,
mutated pack copies) so the suite never damages the real install.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from packs.doctor import (
    FAIL,
    PASS,
    SKIP,
    check_artifact_store,
    check_entry_points,
    check_manifest_hash,
    check_package_shadowing,
    check_python_version,
    check_runtime_version,
    check_store_path,
    main,
    run_doctor,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- helpers


@dataclasses.dataclass(frozen=True)
class _OldCapabilityDecl:
    provider: str
    capability: str
    risk_class: str
    credential_ref: str = ""


def _old_runtime_stub() -> ModuleType:
    stub = ModuleType("activegraph")
    stub.__version__ = "1.7.1"
    stub.packs = SimpleNamespace(
        manifest=SimpleNamespace(CapabilityDecl=_OldCapabilityDecl)
    )
    return stub


class _FakeEntryPoint:
    def __init__(self, name: str, value: str, ok: bool) -> None:
        self.name = name
        self.value = value
        self._ok = ok

    def load(self):
        if not self._ok:
            raise ImportError(f"No module named {self.value.split(':')[0]!r}")
        return object()


# ------------------------------------------------- 1. runtime version/floor


def test_runtime_version_fails_on_old_runtime_with_preflight_message() -> None:
    result = check_runtime_version(_old_runtime_stub())
    assert result.status == FAIL
    assert "activegraph >= 1.9 is required (found 1.7.1)" in result.detail
    assert 'pip install -e "./activegraph-src[llm]"' in result.detail


def test_runtime_version_passes_on_current_runtime() -> None:
    result = check_runtime_version()
    assert result.status == PASS
    assert "CapabilityDecl.action_class present" in result.detail


# ------------------------------------------------------ 2. package shadowing


def test_shadowing_fails_on_namespace_directory() -> None:
    """The classic: the runtime repo cloned as ./activegraph — Python
    resolves a namespace dir with no __init__ and no attributes."""
    result = check_package_shadowing(
        origin=None, namespace_locations=["/work/activegraph"]
    )
    assert result.status == FAIL
    assert "/work/activegraph" in result.detail
    assert "activegraph-src" in result.detail


def test_shadowing_fails_when_import_does_not_match_install(tmp_path) -> None:
    stray = tmp_path / "cwd" / "activegraph" / "__init__.py"
    stray.parent.mkdir(parents=True)
    stray.write_text("")
    installed = tmp_path / "site-packages"
    installed.mkdir()
    result = check_package_shadowing(
        origin=str(stray), installed_roots=[installed]
    )
    assert result.status == FAIL
    assert str(stray) in result.detail  # the resolved __file__ is printed
    assert "shadowing" in result.detail


def test_shadowing_fails_when_pip_has_no_record() -> None:
    result = check_package_shadowing(
        origin="/somewhere/activegraph/__init__.py", installed_roots=None
    )
    assert result.status == FAIL
    assert "pip has no record" in result.detail


def test_shadowing_passes_on_current_environment() -> None:
    result = check_package_shadowing()
    assert result.status == PASS
    assert "__init__.py" in result.detail  # resolved __file__ printed


# -------------------------------------------------- 3. artifact-store (B1)


def test_artifact_store_fails_on_divergent_dirs() -> None:
    result = check_artifact_store(
        configured={
            "activity_normalizer": "/data/a",
            "importer_local_files": "/data/b",
            "importer_chatgpt_export": "/data/a",
        }
    )
    assert result.status == FAIL
    assert "ReplayUnavailableError" in result.detail
    assert "importer_local_files=/data/b" in result.detail


requires_permission_enforcement = pytest.mark.skipif(
    os.geteuid() == 0,
    reason="chmod-based unwritability fixtures are ineffective as root",
)


@requires_permission_enforcement
def test_artifact_store_fails_on_unwritable_dir(tmp_path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = check_artifact_store(
            artifact_dir=str(locked),
            configured={"activity_normalizer": str(locked)},
        )
        assert result.status == FAIL
        assert "not writable" in result.detail
    finally:
        locked.chmod(stat.S_IRWXU)


def test_artifact_store_passes_on_current_settings(tmp_path) -> None:
    # Defaults collected from the real settings classes must agree.
    result = check_artifact_store(artifact_dir=str(tmp_path / "artifacts"))
    assert result.status == PASS


# ------------------------------------------------------- 4. python version


def test_python_version_fails_below_floor() -> None:
    result = check_python_version(version_info=(3, 9, 6))
    assert result.status == FAIL
    assert "Python 3.9.6 is too old" in result.detail
    assert "requires Python >= 3.11" in result.detail


def test_python_version_passes_on_current_interpreter() -> None:
    result = check_python_version()
    assert result.status == PASS


# --------------------------------------------------------- 5. --store path


def test_store_path_skips_without_store() -> None:
    result = check_store_path(None)
    assert result.status == SKIP


def test_store_path_accepts_sqlite_url_and_plain_path(tmp_path) -> None:
    url = f"sqlite:///{tmp_path}/babyagi.db"
    assert check_store_path(url).status == PASS
    assert check_store_path(str(tmp_path / "plain.db")).status == PASS


@requires_permission_enforcement
def test_store_path_fails_on_unwritable_parent(tmp_path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = check_store_path(str(locked / "babyagi.db"))
        assert result.status == FAIL
        assert "cannot be created" in result.detail
    finally:
        locked.chmod(stat.S_IRWXU)


def test_store_path_fails_on_directory_target(tmp_path) -> None:
    result = check_store_path(str(tmp_path))
    assert result.status == FAIL
    assert "is a directory, not a SQLite file" in result.detail


@requires_permission_enforcement
def test_store_path_fails_on_readonly_existing_file(tmp_path) -> None:
    db = tmp_path / "babyagi.db"
    db.write_bytes(b"")
    db.chmod(stat.S_IRUSR)
    try:
        result = check_store_path(str(db))
        assert result.status == FAIL
        assert "exists but is not writable" in result.detail
    finally:
        db.chmod(stat.S_IRUSR | stat.S_IWUSR)


# ---------------------------------------------------------- 6. entry points


def test_entry_points_fail_when_group_is_empty() -> None:
    result = check_entry_points(entry_points=[])
    assert result.status == FAIL
    assert "pip install -e ." in result.detail


def test_entry_points_fail_when_one_does_not_resolve() -> None:
    result = check_entry_points(
        entry_points=[
            _FakeEntryPoint("core", "packs.core:pack", ok=True),
            _FakeEntryPoint("ghost", "packs.ghost:pack", ok=False),
        ]
    )
    assert result.status == FAIL
    assert "1 of 2 pack entry points failed" in result.detail
    assert "ghost = packs.ghost:pack" in result.detail


def test_entry_points_pass_on_current_environment() -> None:
    result = check_entry_points()
    assert result.status == PASS


# --------------------------------------------------------- 7. manifest hash


def test_manifest_hash_fails_on_drifted_pack_copy(tmp_path) -> None:
    fake_root = tmp_path / "repo"
    dst = fake_root / "packs" / "core"
    shutil.copytree(REPO_ROOT / "packs" / "core", dst)
    (dst / "__init__.py").write_text(
        (dst / "__init__.py").read_text() + "\n# drifted\n"
    )
    result = check_manifest_hash(pack="core", repo_root=fake_root)
    assert result.status == FAIL
    assert "generate_manifests.py core" in result.detail


def test_manifest_hash_passes_on_current_checkout() -> None:
    result = check_manifest_hash()
    assert result.status == PASS


def test_manifest_hash_skips_outside_a_checkout(tmp_path) -> None:
    result = check_manifest_hash(repo_root=tmp_path)
    assert result.status == SKIP


# ------------------------------------------------------------ CLI contract


def test_cli_healthy_environment_exits_zero(capsys) -> None:
    code = main([])
    out = capsys.readouterr().out
    assert code == 0
    assert "activegraph-packs doctor" in out
    assert "0 failed" in out


def test_cli_json_is_parseable_and_reports_ok(capsys, tmp_path) -> None:
    code = main(["--json", "--store", f"sqlite:///{tmp_path}/babyagi.db"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["status"] == "ok"
    names = [c["name"] for c in report["checks"]]
    assert names == [
        "runtime-version",
        "package-shadowing",
        "artifact-store",
        "python-version",
        "store-path",
        "entry-points",
        "manifest-hash",
        "llm-provider",
    ]
    assert all(c["status"] in {"pass", "skip"} for c in report["checks"])


@requires_permission_enforcement
def test_cli_broken_store_exits_one(capsys, tmp_path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        code = main(["--store", str(locked / "babyagi.db")])
        out = capsys.readouterr().out
        assert code == 1
        assert "FAIL store-path" in out
    finally:
        locked.chmod(stat.S_IRWXU)


def test_module_invocation_works_as_documented() -> None:
    """python -m packs.doctor is the documented entry — prove it runs."""
    proc = subprocess.run(
        [sys.executable, "-m", "packs.doctor", "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["status"] == "ok"


def test_skip_preflight_escape_hatch_lets_doctor_import(tmp_path) -> None:
    """On a broken runtime the doctor must still be importable via
    ACTIVEGRAPH_PACKS_SKIP_PREFLIGHT=1 (packs/__init__ documents this)."""
    env = dict(os.environ, ACTIVEGRAPH_PACKS_SKIP_PREFLIGHT="1")
    proc = subprocess.run(
        [sys.executable, "-c", "import packs; print('imported')"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "imported" in proc.stdout
