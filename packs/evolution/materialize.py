"""Materialize a proposal's artifacts into an importable pack.

The graph stores the source (artifact objects); this module turns it back
into files and a live pack object. Three callers, one code path: the
fork trial, the adoption processor, and boot-time reload, so what was
hashed is always what loads.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
from pathlib import Path

from activegraph.packs.manifest import compute_bundle_hash, verify_bundle_hash


def proposal_files(graph, proposal) -> dict[str, str]:
    """Reassemble {relative_path: text} from a proposal's artifacts."""
    files = {}
    for artifact_id in proposal.data.get("source_artifact_ids", []):
        obj = graph.get_object(artifact_id)
        if obj is None:
            raise RuntimeError(f"missing source artifact {artifact_id}")
        files[obj.data["title"]] = obj.data["content"]
    return files


def write_files(files: dict[str, str], root: str | Path | None = None,
                pack_name: str = "") -> Path:
    """Write the file dict under a fresh directory; returns the pack root.

    With *pack_name*, the pack root is ``<tmp>/<pack_name>/`` so the
    candidate's own fixtures can ``sys.path.insert`` the parent and
    import the pack by name (the self-contained fixture pattern)."""
    base = Path(root) if root else Path(tempfile.mkdtemp(prefix="evolution_"))
    if pack_name:
        base = base / pack_name
        base.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        target = base / rel
        if ".." in Path(rel).parts:
            raise RuntimeError(f"path escapes pack root: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return base


def bundle_hash_of(files: dict[str, str]) -> str:
    """The proposal pin: runtime bundle hash over a scratch materialization."""
    return compute_bundle_hash(write_files(files))


def import_pack(pack_root: Path, pack_name: str, bundle_hash: str):
    """Import the materialized package and return its ``pack`` object.

    The module name is content-addressed (name + hash prefix) so two
    versions of one agent pack never collide in sys.modules, and
    re-importing the same bytes reuses the cached module."""
    digest = hashlib.sha256(bundle_hash.encode()).hexdigest()[:12]
    module_name = f"_evolution_{pack_name}_{digest}"
    if module_name in sys.modules:
        return sys.modules[module_name].pack
    spec = importlib.util.spec_from_file_location(
        module_name, pack_root / "__init__.py",
        submodule_search_locations=[str(pack_root)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module.pack


def materialize_verified(graph, proposal):
    """Files -> disk -> bundle-hash check -> imported pack.

    The single sanctioned path from graph to loadable code: every caller
    gets the hash pin for free, and a mismatch raises before any import
    executes (PackManifestError from the runtime)."""
    files = proposal_files(graph, proposal)
    root = write_files(files, pack_name=proposal.data["pack_name"])
    verify_bundle_hash(proposal.data["bundle_hash"], root)
    pack = import_pack(root, proposal.data["pack_name"],
                       proposal.data["bundle_hash"])
    return files, root, pack
