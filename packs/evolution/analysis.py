"""Static analysis for agent-authored pack source (design §3 stage 2).

Pure functions over file-content dicts ({relative_path: text}); no disk,
no imports of candidate code, no execution. Each returns a list of
violation strings; empty means the check passed. The honesty note from
the design applies to everything here: static analysis of Python is a
filter that catches mistakes and raises the cost of casual badness. The
containment that holds is the approval structure around it.
"""

from __future__ import annotations

import ast

RUNTIME_FILES = ("__init__.py", "object_types.py", "behaviors.py",
                 "tools.py", "settings.py")
FIXTURE_FILE = "fixtures/run_fixtures.py"

_BANNED_CALLS = {"exec", "eval", "compile", "__import__"}


def _parse(files: dict[str, str]) -> dict[str, ast.AST] | list[str]:
    trees = {}
    for path, text in files.items():
        if not path.endswith(".py"):
            continue
        try:
            trees[path] = ast.parse(text)
        except SyntaxError as exc:
            return [f"{path}: syntax error: {exc.msg} (line {exc.lineno})"]
    return trees


def check_file_set(files: dict[str, str], allowed: list[str]) -> list[str]:
    """Gate: the fixed authored file set, nothing more, fixtures mandatory."""
    violations = []
    for path in files:
        if path not in allowed:
            violations.append(f"file not in the authored set: {path}")
    for required in ("manifest.toml", "__init__.py", FIXTURE_FILE):
        if required not in files:
            violations.append(f"missing required file: {required}")
    return violations


def check_fixture_entrypoint(files: dict[str, str]) -> list[str]:
    """The trial-child contract: the fixture file must define a
    module-level `def main(rt)` the sandbox scenario loader can call."""
    text = files.get(FIXTURE_FILE, "")
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [f"{FIXTURE_FILE}: syntax error: {exc.msg} (line {exc.lineno})"]
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return []
    return [f"{FIXTURE_FILE}: no module-level `def main(rt)` (the trial "
            "child's scenario entrypoint)"]


def check_size_caps(files: dict[str, str], *, max_total: int,
                    max_file: int) -> list[str]:
    """Gate 7: the review-surface guarantee."""
    violations = []
    total = 0
    for path, text in files.items():
        size = len(text.encode())
        total += size
        if size > max_file:
            violations.append(f"{path}: {size} bytes exceeds per-file cap {max_file}")
    if total > max_total:
        violations.append(f"total source {total} bytes exceeds cap {max_total}")
    return violations


def check_imports(files: dict[str, str], allow: list[str],
                  fixture_extra: list[str], pack_name: str = "") -> list[str]:
    """Gate 4: import allow-list, fixtures exempted onto their own list.

    A candidate's fixture file may import the pack by its OWN name (the
    self-contained fixture pattern); runtime source may not, it uses
    relative imports."""
    trees = _parse(files)
    if isinstance(trees, list):
        return trees
    violations = []
    for path, tree in trees.items():
        allowed = set(allow)
        if path == FIXTURE_FILE:
            allowed |= set(fixture_extra)
            if pack_name:
                allowed.add(pack_name)
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue  # intra-pack relative import: allowed
                names = [node.module or ""]
            for name in names:
                root_ok = any(name == entry or name.startswith(entry + ".")
                              for entry in allowed)
                if not root_ok:
                    violations.append(f"{path}: import {name!r} not in allow-list")
    return violations


def check_banned_constructs(files: dict[str, str]) -> list[str]:
    """Gate 5: exec/eval/compile/__import__, computed getattr names."""
    trees = _parse(files)
    if isinstance(trees, list):
        return trees
    violations = []
    for path, tree in trees.items():
        if path == FIXTURE_FILE:
            continue  # fixtures governed by gate 4's fixture list + determinism
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None)
                if name in _BANNED_CALLS:
                    violations.append(f"{path}: banned call {name}()")
                if name == "getattr" and len(node.args) >= 2:
                    second = node.args[1]
                    if not (isinstance(second, ast.Constant)
                            and isinstance(second.value, str)):
                        violations.append(f"{path}: getattr with computed name")
    return violations


def _decorator_names(node: ast.FunctionDef) -> dict[str, str]:
    """{decorator_kind: declared name} for behavior/tool decorators."""
    out = {}
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call):
            kind = getattr(dec.func, "id", None) or getattr(dec.func, "attr", None)
            if kind in ("behavior", "llm_behavior", "tool"):
                declared = node.name
                for kw in dec.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        declared = kw.value.value
                out[kind] = declared
        else:
            kind = getattr(dec, "id", None) or getattr(dec, "attr", None)
            if kind in ("behavior", "llm_behavior", "tool"):
                out[kind] = node.name
    return out


def extract_surface(files: dict[str, str]) -> dict[str, set[str]]:
    """The ACTUAL surface found in source: behaviors, tools, object types,
    relation types, and gateway capability registrations (provider.capability)."""
    trees = _parse(files)
    if isinstance(trees, list):
        raise ValueError("; ".join(trees))
    surface = {"behaviors": set(), "tools": set(), "object_types": set(),
               "relation_types": set(), "capabilities": set()}
    for path, tree in trees.items():
        if path == FIXTURE_FILE:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for kind, name in _decorator_names(node).items():
                    key = "tools" if kind == "tool" else "behaviors"
                    surface[key].add(name)
            if isinstance(node, ast.Call):
                cname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if cname in ("ObjectType", "RelationType"):
                    for kw in node.keywords:
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                            key = ("object_types" if cname == "ObjectType"
                                   else "relation_types")
                            surface[key].add(kw.value.value)
                if cname == "register_local_capability" and len(node.args) >= 2:
                    a0, a1 = node.args[0], node.args[1]
                    if (isinstance(a0, ast.Constant) and isinstance(a1, ast.Constant)):
                        surface["capabilities"].add(f"{a0.value}.{a1.value}")
    return surface


def check_declared_vs_actual(files: dict[str, str], manifest) -> list[str]:
    """Gate 3: two-way, both directions are violations."""
    actual = extract_surface(files)
    declared = {
        "behaviors": set(manifest.behaviors),
        "tools": set(manifest.tools),
        "object_types": set(manifest.object_types),
        "relation_types": set(manifest.relation_types),
        "capabilities": {f"{c.provider}.{c.capability}" for c in manifest.capabilities},
    }
    violations = []
    for kind in declared:
        for name in sorted(declared[kind] - actual[kind]):
            violations.append(f"{kind}: declared {name!r} not found in source")
        for name in sorted(actual[kind] - declared[kind]):
            violations.append(f"{kind}: source defines {name!r} undeclared")
    return violations


def check_reserved_namespaces(files: dict[str, str], manifest,
                              reserved: list[str]) -> list[str]:
    """Gate 6: no registrations into governance namespaces, no
    NEVER_LLM_CALLABLE names under any provider."""
    from packs.tool_gateway.untrusted import NEVER_LLM_CALLABLE

    violations = []
    surface = extract_surface(files)
    declared_caps = {f"{c.provider}.{c.capability}" for c in manifest.capabilities}
    for key in sorted(surface["capabilities"] | declared_caps):
        provider, _, capability = key.partition(".")
        if provider in reserved:
            violations.append(f"capability {key!r} registers into reserved "
                              f"namespace {provider!r}")
        if capability in NEVER_LLM_CALLABLE:
            violations.append(f"capability {key!r} uses a never-LLM-callable "
                              f"name ({capability!r})")
    for name in sorted(surface["tools"] | surface["behaviors"]):
        if name in NEVER_LLM_CALLABLE:
            violations.append(f"{name!r} collides with NEVER_LLM_CALLABLE")
    if manifest.name in reserved:
        violations.append(f"pack name {manifest.name!r} is reserved")
    return violations
