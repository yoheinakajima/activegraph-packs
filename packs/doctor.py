"""``python -m packs.doctor`` — noninteractive environment diagnostic (H4).

Built from three real fresh-machine incidents:

  1. a PyPI runtime older than this library's contract failing weird at
     import (``CapabilityDecl.__init__() got an unexpected keyword
     argument 'action_class'``) — the H2 preflight, surfaced as a
     pass/fail line instead of a raise;
  2. a stray ``activegraph`` directory on ``sys.path`` shadowing the
     installed package (clone the runtime repo as ``activegraph-src``,
     never ``activegraph``);
  3. importer and normalizer configured with different replay artifact
     stores — every imported item then dies at replay with
     ``ReplayUnavailableError`` (the B1 gotcha).

Plus cheap extras: Python floor, ``--store`` writability, pack entry
points, and a manifest content-hash spot-check.

Exit code 0 when every check passes (skips are fine), 1 otherwise.
``--json`` emits a machine-readable report for tooling.

If even ``import packs`` fails on this environment, the preflight
error IS the diagnosis; to run the remaining checks anyway:

    ACTIVEGRAPH_PACKS_SKIP_PREFLIGHT=1 python -m packs.doctor

Checks take injectable inputs so tests can simulate broken
environments without breaking the one they run in.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.metadata
import importlib.util
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

PASS = "pass"
FAIL = "fail"
SKIP = "skip"

#: Python floor per pyproject ``requires-python``. The runtime itself
#: requires 3.11; a 3.10 interpreter dies on 3.11-only syntax far less
#: legibly than this line does.
PYTHON_FLOOR = (3, 11)

_REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclasses.dataclass(frozen=True)
class CheckResult:
    name: str
    status: str  # pass | fail | skip
    detail: str

    def as_dict(self) -> dict[str, str]:
        return dataclasses.asdict(self)


def check_runtime_version(
    runtime_module: ModuleType | None = None,
) -> CheckResult:
    """Incident 1: the H2 preflight, reported instead of raised."""
    name = "runtime-version"
    try:
        from packs._preflight import (
            REQUIRED_FLOOR,
            found_runtime_version,
            preflight_message,
            runtime_supports_contract,
        )

        supported = runtime_supports_contract(runtime_module)
        found = found_runtime_version(runtime_module)
    except Exception as exc:  # pragma: no cover — runtime so broken it raises
        return CheckResult(
            name,
            FAIL,
            f"could not probe the activegraph runtime at all ({exc!r}). "
            f"Is activegraph installed in this environment? "
            f'pip install -e "./activegraph-src[llm]" (clone as '
            f"activegraph-src — a dir named activegraph would shadow "
            f"the package). See README quickstart.",
        )
    if not supported:
        return CheckResult(name, FAIL, preflight_message(found))
    detail = (
        f"activegraph {found} meets the v{REQUIRED_FLOOR} contract floor "
        f"(CapabilityDecl.action_class present)"
    )
    if _version_strictly_below(found, (1, 9)):
        detail += (
            " — note: the version string lags the code (editable install?);"
            " the feature floor is what packs actually need"
        )
    return CheckResult(name, PASS, detail)


def _version_strictly_below(version: str, floor: tuple[int, int]) -> bool:
    parts: list[int] = []
    for chunk in version.split(".")[:2]:
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if not digits:
            return False  # unparseable — don't claim it lags
        parts.append(int(digits))
    return tuple(parts + [0] * (2 - len(parts))) < floor


def check_package_shadowing(
    origin: str | None | Any = ...,
    namespace_locations: Sequence[str] | None = None,
    installed_roots: Sequence[Path] | None | Any = ...,
) -> CheckResult:
    """Incident 2: is ``import activegraph`` the installed package, or a
    stray directory on ``sys.path``? Always prints the resolved file."""
    name = "package-shadowing"
    if origin is ...:
        spec = importlib.util.find_spec("activegraph")
        if spec is None:
            return CheckResult(
                name,
                FAIL,
                "activegraph is not importable in this environment. "
                'Install the runtime: pip install -e "./activegraph-src[llm]" '
                "(clone as activegraph-src — a dir named activegraph would "
                "shadow the package). See README quickstart.",
            )
        origin = spec.origin
        namespace_locations = list(spec.submodule_search_locations or [])
    if origin is None:
        where = namespace_locations[0] if namespace_locations else "<unknown>"
        return CheckResult(
            name,
            FAIL,
            f"import activegraph resolves to a namespace directory "
            f"({where}), not an installed package — a stray directory "
            f"named activegraph on sys.path (usually a clone of the "
            f"runtime repo in your working directory) is shadowing the "
            f"real install. Rename the clone to activegraph-src or run "
            f"Python from a different directory.",
        )
    if installed_roots is ...:
        try:
            installed_roots = _installed_activegraph_roots()
        except importlib.metadata.PackageNotFoundError:
            installed_roots = None
    if installed_roots is None:
        return CheckResult(
            name,
            FAIL,
            f"import activegraph resolves to {origin}, but pip has no "
            f"record of installing activegraph — you are importing a "
            f"stray source tree on sys.path, not an install. "
            f'pip install -e "./activegraph-src[llm]" (clone as '
            f"activegraph-src — a dir named activegraph would shadow "
            f"the package).",
        )
    resolved = Path(origin).resolve()
    for root in installed_roots:
        if root in resolved.parents or root == resolved.parent:
            kind = "editable install" if root not in _site_dirs() else "site-packages"
            return CheckResult(
                name, PASS, f"activegraph imports from {resolved} ({kind}, under {root})"
            )
    return CheckResult(
        name,
        FAIL,
        f"import activegraph resolves to {resolved}, but pip installed "
        f"it under {', '.join(str(r) for r in installed_roots)}. A "
        f"directory earlier on sys.path is shadowing the installed "
        f"package — usually a stray activegraph directory in your "
        f"working directory. Rename or remove it, or run Python from "
        f"another directory.",
    )


def _installed_activegraph_roots() -> list[Path]:
    """Where pip believes activegraph lives: the site dir for a normal
    install, the source tree for a PEP 660 editable install."""
    dist = importlib.metadata.distribution("activegraph")
    roots = [Path(str(dist.locate_file(""))).resolve()]
    direct_url = dist.read_text("direct_url.json")
    if direct_url:
        try:
            info = json.loads(direct_url)
        except ValueError:
            info = {}
        url = info.get("url", "")
        if info.get("dir_info", {}).get("editable") and url.startswith("file://"):
            roots.append(Path(url[len("file://"):]).resolve())
    return roots


def _site_dirs() -> set[Path]:
    import site

    dirs = {Path(p).resolve() for p in site.getsitepackages()}
    dirs.add(Path(site.getusersitepackages()).resolve())
    return dirs


def check_artifact_store(
    artifact_dir: str | None = None,
    configured: Mapping[str, str] | None = None,
) -> CheckResult:
    """Incident 3 (B1): the replay artifact store must be one directory,
    shared by importer and normalizer settings, and writable."""
    name = "artifact-store"
    if configured is None:
        try:
            from packs.activity_normalizer.settings import (
                ActivityNormalizerSettings,
            )
            from packs.importers.chatgpt_export.settings import (
                ChatGPTExportSettings,
            )
            from packs.importers.local_files.settings import (
                LocalFilesSettings,
            )

            configured = {
                "activity_normalizer": ActivityNormalizerSettings().artifact_store_dir,
                "importer_local_files": LocalFilesSettings().artifact_store_dir,
                "importer_chatgpt_export": ChatGPTExportSettings().artifact_store_dir,
            }
        except Exception as exc:
            return CheckResult(
                name,
                FAIL,
                f"could not load pack settings to compare artifact store "
                f"dirs ({exc!r}) — fix the runtime-version / shadowing "
                f"failures above first, then re-run the doctor.",
            )
    if len(set(configured.values())) > 1:
        listing = ", ".join(f"{k}={v}" for k, v in sorted(configured.items()))
        return CheckResult(
            name,
            FAIL,
            f"importer and normalizer artifact stores diverge: {listing}. "
            f"The importer writes replay artifacts where the normalizer "
            f"will not look, so every imported item fails at replay with "
            f"ReplayUnavailableError. Configure ONE shared "
            f"artifact_store_dir across activity_normalizer and the "
            f"importer packs.",
        )
    effective = Path(artifact_dir or next(iter(configured.values())))
    return _writability(name, effective, kind="artifact store directory",
                        shared_note=" (shared by importer + normalizer settings)")


def check_python_version(
    version_info: tuple[int, int, int] | None = None,
) -> CheckResult:
    name = "python-version"
    vi = version_info or sys.version_info[:3]
    version = ".".join(str(part) for part in vi)
    floor = ".".join(str(part) for part in PYTHON_FLOOR)
    if vi[:2] < PYTHON_FLOOR:
        return CheckResult(
            name,
            FAIL,
            f"Python {version} is too old: activegraph-packs requires "
            f"Python >= {floor} (pyproject requires-python). On macOS the "
            f"system python is 3.9 — install a newer one (e.g. brew "
            f"install python@3.11) and rebuild the venv with it.",
        )
    return CheckResult(name, PASS, f"Python {version} >= {floor}")


def check_store_path(store: str | None) -> CheckResult:
    """--store writability: the engine must be able to create/append the
    SQLite event store the caller asked for."""
    name = "store-path"
    if store is None:
        return CheckResult(name, SKIP, "no --store given; nothing to check")
    raw = store
    for prefix in ("sqlite:///", "sqlite://"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    path = Path(raw)
    if path.exists():
        if path.is_dir():
            return CheckResult(
                name,
                FAIL,
                f"store path {path} is a directory, not a SQLite file. "
                f"Point --store at a file path, e.g. "
                f"sqlite:///{path}/babyagi.db",
            )
        if os.access(path, os.W_OK):
            return CheckResult(name, PASS, f"store file {path} exists and is writable")
        return CheckResult(
            name,
            FAIL,
            f"store file {path} exists but is not writable. The engine "
            f"cannot append events to it — fix permissions (chmod u+w "
            f"{path}) or point --store at a writable path.",
        )
    parent = path.parent if str(path.parent) else Path(".")
    probe = _writability(name, parent, kind="parent directory")
    if probe.status == FAIL:
        return CheckResult(
            name,
            FAIL,
            f"store path {path} cannot be created: {probe.detail} Point "
            f"--store at a writable location or create the directory "
            f"first.",
        )
    return CheckResult(
        name,
        PASS,
        f"store {path} does not exist yet and its parent is writable; "
        f"the engine will create it on first boot",
    )


def _writability(
    name: str,
    directory: Path,
    kind: str,
    shared_note: str = "",
) -> CheckResult:
    """PASS iff *directory* (or its nearest existing ancestor) accepts a
    write. Never creates anything permanent."""
    probe = directory
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            break
        probe = parent
    try:
        with tempfile.NamedTemporaryFile(dir=probe, prefix=".doctor-probe-"):
            pass
    except OSError as exc:
        return CheckResult(
            name,
            FAIL,
            f"{kind} {directory} is not writable ({exc}). Nothing can be "
            f"persisted there — fix permissions or configure a writable "
            f"path instead.",
        )
    if probe == directory.resolve() or probe == directory:
        return CheckResult(
            name, PASS, f"{kind} {directory}{shared_note} is writable"
        )
    return CheckResult(
        name,
        PASS,
        f"{kind} {directory}{shared_note} does not exist yet; "
        f"nearest existing ancestor {probe} is writable, so it will be "
        f"created on first write",
    )


def check_entry_points(
    entry_points: Sequence[importlib.metadata.EntryPoint] | None = None,
) -> CheckResult:
    """Every pack registered under ``activegraph.packs`` must resolve,
    or ``load_by_name`` fails at the first lookup."""
    name = "entry-points"
    if entry_points is None:
        entry_points = list(
            importlib.metadata.entry_points(group="activegraph.packs")
        )
    if not entry_points:
        return CheckResult(
            name,
            FAIL,
            "no entry points registered under group 'activegraph.packs' — "
            "activegraph-packs is not installed in this environment "
            "(running from a source checkout without pip install -e .?). "
            "Pack loading by name will find nothing. Run: pip install -e .",
        )
    failures: list[str] = []
    for ep in entry_points:
        try:
            ep.load()
        except Exception as exc:
            failures.append(f"{ep.name} = {ep.value}: {exc!r}")
    if failures:
        joined = "\n  ".join(failures)
        return CheckResult(
            name,
            FAIL,
            f"{len(failures)} of {len(entry_points)} pack entry points "
            f"failed to resolve:\n  {joined}\nEach names a module that no "
            f"longer imports — usually a half-installed or stale "
            f"environment. Reinstall with: pip install -e .",
        )
    return CheckResult(
        name, PASS, f"all {len(entry_points)} pack entry points resolve"
    )


def check_manifest_hash(
    pack: str = "core", repo_root: Path | None = None
) -> CheckResult:
    """Spot-check one pack's manifest content hash against its files —
    the same condition CI's drift gate enforces."""
    name = "manifest-hash"
    root = repo_root or _REPO_ROOT
    manifest_path = root / "packs" / pack / "manifest.toml"
    if not manifest_path.exists():
        return CheckResult(
            name,
            SKIP,
            f"no {manifest_path} (not running from a source checkout); "
            f"nothing to spot-check",
        )
    try:
        from activegraph.packs.manifest import load_manifest, verify_content_hash

        manifest = load_manifest(manifest_path)
        verify_content_hash(manifest, manifest_path.parent)
    except Exception as exc:
        return CheckResult(
            name,
            FAIL,
            f"packs/{pack} manifest hash spot-check failed: {exc}. The "
            f"pack's files changed without regenerating its manifest — "
            f"run: python scripts/generate_manifests.py {pack} (CI's "
            f"drift gate fails on the same condition).",
        )
    return CheckResult(
        name,
        PASS,
        f"packs/{pack} manifest content hash verifies "
        f"({manifest.content_hash[:19]}…)",
    )


def run_doctor(
    store: str | None = None, artifact_dir: str | None = None
) -> list[CheckResult]:
    return [
        check_runtime_version(),
        check_package_shadowing(),
        check_artifact_store(artifact_dir=artifact_dir),
        check_python_version(),
        check_store_path(store),
        check_entry_points(),
        check_manifest_hash(),
    ]


def render_text(results: Sequence[CheckResult]) -> str:
    lines = ["activegraph-packs doctor"]
    for r in results:
        lines.append(f"  {r.status.upper():4} {r.name:18} {r.detail}")
    passed = sum(r.status == PASS for r in results)
    failed = sum(r.status == FAIL for r in results)
    skipped = sum(r.status == SKIP for r in results)
    lines.append(
        f"doctor: {len(results)} checks — {passed} passed, "
        f"{failed} failed, {skipped} skipped"
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m packs.doctor",
        description="Noninteractive environment diagnostic for activegraph-packs.",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit a machine-readable JSON report"
    )
    parser.add_argument(
        "--store",
        default=None,
        help="event-store URL or path to check for writability "
        "(sqlite:///path/to/babyagi.db or a plain path)",
    )
    parser.add_argument(
        "--artifact-dir",
        default=None,
        help="replay artifact store directory to check instead of the "
        "settings default",
    )
    args = parser.parse_args(argv)
    results = run_doctor(store=args.store, artifact_dir=args.artifact_dir)
    ok = not any(r.status == FAIL for r in results)
    if args.json:
        print(
            json.dumps(
                {
                    "status": "ok" if ok else "fail",
                    "checks": [r.as_dict() for r in results],
                },
                indent=2,
            )
        )
    else:
        print(render_text(results))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
