"""Shared manifest tooling: static capability extraction + generation.

Two jobs, one module, so the generator and the CI check can never
disagree about what "the capabilities a pack's source declares" means:

  * ``extract_capability_registrations(pack_dir)`` walks the pack's
    source for ``register_local_capability(provider, capability, ...)``
    call sites with LITERAL provider/capability arguments and returns
    CapabilityDecl-shaped dicts (risk_class/credential_ref picked up
    from literal keywords, defaulting like the registry does).
  * ``extract_consumed_capabilities(pack_dir)`` walks for behavior-side
    capability invocation: ``add_object("capability_call", {...})``
    sites whose provider_name/capability_name are string literals.
    Dynamic sites (variables, f-strings) are invisible to static
    analysis by nature; the manifest's ``consumes`` is therefore
    checked one-way (declared ⊇ statically visible), and the runtime's
    Q8 mechanism chain (Pack.capabilities + gateway-side registration
    checks) is the eventual stronger enforcement.

This is the AST layer the manifest spec assigns to this repo (spec §3:
"statically verified, never by the loader"). Validation and hashing are
NOT here: those are imported from ``activegraph.packs.manifest``, the
runtime-owned reference implementation.
"""

from __future__ import annotations

import ast
from pathlib import Path

_RISK_DEFAULT = "low"


def _literal(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _iter_pack_sources(pack_dir: Path):
    for path in sorted(pack_dir.rglob("*.py")):
        if "__pycache__" in path.parts or "fixtures" in path.parts:
            continue
        yield path


def _param_default(func_node: ast.FunctionDef | None, param: str) -> str | None:
    """The literal default of *param* on *func_node*, if any.

    Registration helpers follow the `def register_x(*, risk_class="high")`
    pattern and pass the parameter through; resolving the default keeps the
    static declaration honest for that idiom."""
    if func_node is None:
        return None
    args = func_node.args
    pos = args.posonlyargs + args.args
    n_def = len(args.defaults)
    for a, d in zip(pos[len(pos) - n_def:], args.defaults):
        if a.arg == param:
            return _literal(d)
    for a, d in zip(args.kwonlyargs, args.kw_defaults):
        if a.arg == param and d is not None:
            return _literal(d)
    return None


def extract_capability_registrations(pack_dir: str | Path) -> list[dict]:
    """CapabilityDecl-shaped dicts for every literal register_local_capability
    call site in the pack's runtime source (fixtures excluded).

    Keyword values that are literals are taken as-is; a keyword passing a
    plain variable that names a parameter of the ENCLOSING function resolves
    to that parameter's literal default (the register_x(*, risk_class=...)
    pass-through idiom). Anything else stays at the registry default, same
    as the registry itself."""
    declarations: list[dict] = []
    for path in _iter_pack_sources(Path(pack_dir)):
        tree = ast.parse(path.read_text())
        # Module-level string constants (NAME = "literal"), the second
        # resolution source for pass-through keyword values.
        module_constants: dict[str, str] = {}
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                value = _literal(node.value)
                if value is not None:
                    module_constants[node.targets[0].id] = value
        # Map every Call node to its enclosing function for default lookup.
        enclosing: dict[int, ast.FunctionDef] = {}
        for func in ast.walk(tree):
            if isinstance(func, ast.FunctionDef):
                for inner in ast.walk(func):
                    if isinstance(inner, ast.Call):
                        enclosing.setdefault(id(inner), func)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "register_local_capability":
                continue
            if len(node.args) < 2:
                continue
            provider = _literal(node.args[0])
            capability = _literal(node.args[1])
            if provider is None or capability is None:
                continue  # dynamic registration: invisible to static analysis

            def _resolve(kw_value) -> str | None:
                direct = _literal(kw_value)
                if direct is not None:
                    return direct
                if isinstance(kw_value, ast.Name):
                    from_param = _param_default(enclosing.get(id(node)),
                                                kw_value.id)
                    if from_param is not None:
                        return from_param
                    return module_constants.get(kw_value.id)
                return None

            risk = _RISK_DEFAULT
            credential_ref = ""
            action_class = ""  # registry default: undeclared
            for kw in node.keywords:
                if kw.arg == "risk_class":
                    risk = _resolve(kw.value) or risk
                if kw.arg == "credential_ref_name":
                    credential_ref = _resolve(kw.value) or ""
                if kw.arg == "action_class":
                    action_class = _resolve(kw.value) or ""
            declarations.append({
                "provider": provider,
                "capability": capability,
                "risk_class": risk,
                "credential_ref": credential_ref,
                "action_class": action_class,
            })
    declarations.sort(key=lambda d: (d["provider"], d["capability"]))
    return declarations


def extract_consumed_capabilities(pack_dir: str | Path) -> list[str]:
    """Capability keys this pack's source invokes with LITERAL names:
    add_object("capability_call", {...}) sites where provider_name and
    capability_name are string constants in the dict."""
    consumed: set[str] = set()
    for path in _iter_pack_sources(Path(pack_dir)):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name != "add_object" or not node.args:
                continue
            if _literal(node.args[0]) != "capability_call":
                continue
            if len(node.args) < 2 or not isinstance(node.args[1], ast.Dict):
                continue
            fields = {}
            for k, v in zip(node.args[1].keys, node.args[1].values):
                key = _literal(k) if k is not None else None
                if key in ("provider_name", "capability_name"):
                    fields[key] = _literal(v)
            provider = fields.get("provider_name")
            capability = fields.get("capability_name")
            if provider and capability:
                consumed.add(f"{provider}.{capability}")
    return sorted(consumed)
