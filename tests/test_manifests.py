"""Every pack's manifest, validated on every push.

The manifest spec's CI consumer, live. Validation and hashing come from
the runtime's reference implementation (activegraph.packs.manifest, Q7:
runtime owns schema and validation); the static AST checks for
[[surface.capabilities]] and consumes are this repo's by design (spec
§3: registration is host wiring the loader cannot see).

What a failure here means:
  * verify_content_hash fail: a pack changed without rerunning
    ``python scripts/generate_manifests.py <pack>``. That is the pin
    doing its job; refresh the manifest in the same commit as the code.
  * verify_surface fail: code and declaration disagree (an undeclared
    behavior, a declared-but-missing tool). Fix whichever is wrong.
  * capability/consumes fail: a register_local_capability call site or
    a literal capability invocation is not declared. Declare it.
"""

from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import pytest

from activegraph.packs.manifest import (
    PackManifestError,
    compute_bundle_hash,
    load_manifest,
    verify_bundle_hash,
    verify_content_hash,
    verify_surface,
)

from manifest_tools import (
    extract_capability_registrations,
    extract_consumed_capabilities,
)

REPO = Path(__file__).parents[1]
PACKS = [
    ("core", "core"),
    ("activity_normalizer", "activity_normalizer"),
    ("usage", "usage"),
    ("skills", "skills"),
    ("eval_outcome", "eval_outcome"),
    ("importer_local_files", "importers/local_files"),
    ("importer_chatgpt_export", "importers/chatgpt_export"),
    ("importer_claude_export", "importers/claude_export"),
    ("importer_assistant_self_summary", "importers/assistant_self_summary"),
    ("importer_assistant_local_sessions", "importers/assistant_local_sessions"),
    ("importer_public_presence", "importers/public_presence"),
    ("semantic_extraction", "semantic_extraction"),
    ("attention", "attention"),
    ("tool_gateway", "tool_gateway"),
    ("secrets", "secrets"),
    ("memory_gateway", "memory_gateway"),
    ("identity_auth", "identity_auth"),
    ("agent_profile", "agent_profile"),
    ("entity", "entity"),
    ("communication", "communication"),
    ("chat", "chat"),
    ("email", "email"),
    ("schedule", "schedule"),
    ("telegram", "telegram"),
    ("whatsapp", "whatsapp"),
    ("mcp", "mcp"),
    ("composio", "composio"),
    ("gmail", "gmail"),
    ("research", "research"),
    ("codebase", "codebase"),
    ("team_ops", "team_ops"),
    ("meeting", "meeting"),
    ("bridges", "bridges"),
    ("evolution", "evolution"),
]


def _pack_dir(relative_path: str) -> Path:
    return REPO / "packs" / relative_path


def _pack_module(relative_path: str) -> str:
    return "packs." + relative_path.replace("/", ".")


@pytest.mark.parametrize(
    ("name", "relative_path"), PACKS, ids=[name for name, _ in PACKS]
)
def test_manifest_validates_and_hash_pins(name, relative_path):
    """Runtime-side validation: schema, content hash, two-way surface."""
    pack_dir = _pack_dir(relative_path)
    manifest = load_manifest(pack_dir / "manifest.toml")
    verify_content_hash(manifest, pack_dir)

    pack = importlib.import_module(_pack_module(relative_path)).pack
    verify_surface(manifest, pack)
    assert manifest.name == pack.name
    assert manifest.version == pack.version
    assert manifest.fixtures_entrypoint == "fixtures/run_fixtures.py"
    assert (pack_dir / manifest.fixtures_entrypoint).exists()
    assert manifest.fixtures_deterministic is True


@pytest.mark.parametrize(
    ("name", "relative_path"), PACKS, ids=[name for name, _ in PACKS]
)
def test_capability_declarations_match_source(name, relative_path):
    """This repo's AST half of the two-way check (spec §3): every literal
    register_local_capability site is declared, and vice versa; declared
    consumes covers every literal capability invocation."""
    pack_dir = _pack_dir(relative_path)
    manifest = load_manifest(pack_dir / "manifest.toml")

    declared = {(c.provider, c.capability, c.risk_class, c.action_class)
                for c in manifest.capabilities}
    found = {(d["provider"], d["capability"], d["risk_class"],
              d["action_class"])
             for d in extract_capability_registrations(pack_dir)}
    assert declared == found, (
        f"{name}: [[surface.capabilities]] does not match "
        f"register_local_capability call sites (declared={declared}, "
        f"found={found})"
    )

    consumed_declared = set(manifest.consumes)
    consumed_found = set(extract_consumed_capabilities(pack_dir))
    assert consumed_found <= consumed_declared, (
        f"{name}: literal capability invocations missing from consumes: "
        f"{consumed_found - consumed_declared}"
    )


def test_stale_hash_is_caught(tmp_path):
    """The pin means something: edit a file without regenerating, and
    verification fails loudly with the house error."""
    src = _pack_dir("secrets")
    dst = tmp_path / "secrets"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
    (dst / "tools.py").write_text((dst / "tools.py").read_text() + "\n# tampered\n")
    manifest = load_manifest(dst / "manifest.toml")
    with pytest.raises(PackManifestError):
        verify_content_hash(manifest, dst)


def test_bundle_hash_catches_manifest_only_swap(tmp_path):
    """The §4 two-hash argument, as a test: swapping ONLY manifest.toml
    (relabeling a risk class) passes the content hash (which excludes the
    manifest by necessity) but fails the BUNDLE hash external pins use.
    This is the approve-then-swap case the runtime review closed."""
    src = _pack_dir("telegram")
    dst = tmp_path / "telegram"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))

    pin = compute_bundle_hash(dst)  # the pin taken at review time

    manifest_path = dst / "manifest.toml"
    tampered = manifest_path.read_text().replace(
        'authored_by = "human"', 'authored_by = "agent"')
    assert tampered != manifest_path.read_text(), "fixture assumes a provenance flag"
    manifest_path.write_text(tampered)

    # Content hash still passes: the manifest is not part of its own hash.
    verify_content_hash(load_manifest(manifest_path), dst)
    # The bundle-hash pin catches it.
    with pytest.raises(PackManifestError):
        verify_bundle_hash(pin, dst)
