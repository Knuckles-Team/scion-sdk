"""Static scan of the SDK source for environment reads, shared by the census and the drift check."""

from __future__ import annotations

import ast
import os
import re
from collections.abc import Iterator
from pathlib import Path

ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")

_WALK_SKIP_DIRS = frozenset(
    {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".venv",
     "__pycache__", "build", "dist", "node_modules"}
)  # fmt: skip
_NON_RUNTIME_SOURCE_DIRS = frozenset(
    {"docs", "examples", "reports", "scripts", "test", "tests"}
)
#: These modules spell reader calls in prose; never scan them as readers.
_SELF_DOC_FILES = frozenset(
    {
        "env_drift.py",
        "check_env_var_drift.py",
        "env_scan.py",
        "_env_drift_families.py",
        "_env_drift_declared.py",
    }
)


def walk_files(root: Path, *, suffix: str) -> list[Path]:
    """Source files under ``root``, pruning dependency and cache trees."""
    found: list[Path] = []
    for directory, names, files in os.walk(root):
        names[:] = sorted(
            n for n in names if n not in _WALK_SKIP_DIRS and not n.startswith(".")
        )
        found.extend(Path(directory) / n for n in files if n.endswith(suffix))
    return sorted(found)


def skip_runtime_file(root: Path, py: Path) -> bool:
    try:
        parts = py.relative_to(root).parts
    except ValueError:
        parts = py.parts
    if any(part in _NON_RUNTIME_SOURCE_DIRS for part in parts[:-1]):
        return True
    return py.name in _SELF_DOC_FILES


def parse_python(py: Path) -> ast.AST | None:
    try:
        return ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return None


def runtime_trees(root: Path) -> Iterator[ast.AST]:
    """The parsed runtime ``*.py`` modules under ``root``."""
    for py in walk_files(root, suffix=".py"):
        if skip_runtime_file(root, py):
            continue
        tree = parse_python(py)
        if tree is not None:
            yield tree


def literal_str(node: ast.AST | None) -> str | None:
    """The string value of a string-constant node, else ``None``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def literal_env_name(node: ast.AST | None) -> str | None:
    """The string value of ``node`` when it looks like an env var name."""
    value = literal_str(node)
    return value if value and ENV_NAME.fullmatch(value) else None


def assign_pairs(node: ast.AST) -> tuple[list[ast.expr], ast.expr] | None:
    """``(targets, value)`` of a valued assignment node, else ``None``."""
    if isinstance(node, ast.Assign):
        return node.targets, node.value
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return [node.target], node.value
    return None


def target_name(target: ast.AST) -> str | None:
    """The bare name of a ``Name`` or ``Attribute`` assignment target."""
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def function_params(tree: ast.AST) -> dict[str, list[str]]:
    return {
        node.name: [a.arg for a in node.args.posonlyargs]
        + [a.arg for a in node.args.args]
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def attr_assignments(node: ast.AST, attr: str) -> list[tuple[str, ast.expr]]:
    """``(function, value)`` per ``function.<attr> = value`` target."""
    if not isinstance(node, ast.Assign):
        return []
    return [
        (t.value.id, node.value)
        for t in node.targets
        if isinstance(t, ast.Attribute)
        and isinstance(t.value, ast.Name)
        and t.attr == attr
    ]


def is_environ(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "environ"
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
        and node.attr == "environ"
    )


def _is_env_reader_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in {"_setting", "setting", "getenv"}
    if isinstance(func, ast.Attribute):
        if func.attr == "getenv":
            return isinstance(func.value, ast.Name) and func.value.id == "os"
        return func.attr == "get" and is_environ(func.value)
    return False


def _is_field_call(node: ast.Call) -> bool:
    func = node.func
    return (isinstance(func, ast.Name) and func.id == "Field") or (
        isinstance(func, ast.Attribute) and func.attr == "Field"
    )


def alias_literals(tree: ast.AST) -> dict[str, str]:
    """``NAME = "VAR"`` constants a reader may be passed instead of a literal."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        pair = assign_pairs(node)
        name = literal_env_name(pair[1]) if pair else None
        if pair is None or not name:
            continue
        for target in pair[0]:
            key = target_name(target)
            if key:
                out[key] = name
    return out


def _name_of(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
    literal = literal_env_name(node)
    if literal:
        return literal
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    if isinstance(node, ast.Attribute):
        return aliases.get(node.attr)
    return None


def _call_names(node: ast.Call, aliases: dict[str, str]) -> set[str]:
    found: set[str] = set()
    if _is_env_reader_call(node) and node.args:
        name = _name_of(node.args[0], aliases)
        if name:
            found.add(name)
    if _is_field_call(node):
        aliased = (literal_env_name(k.value) for k in node.keywords if k.arg == "alias")
        found.update(name for name in aliased if name)
    return found


def _node_names(node: ast.AST, aliases: dict[str, str]) -> set[str]:
    if isinstance(node, ast.Call):
        return _call_names(node, aliases)
    if isinstance(node, ast.Subscript):
        loads = isinstance(node.ctx, ast.Load) and is_environ(node.value)
        name = _name_of(node.slice, aliases) if loads else None
        return {name} if name else set()
    pair = assign_pairs(node)
    if pair is None or not any(target_name(t) == "enable_flag" for t in pair[0]):
        return set()
    name = literal_env_name(pair[1])
    return {name} if name else set()


def _env_names_from_tree(tree: ast.AST) -> set[str]:
    """Literal configuration reads in one parsed module (writes excluded)."""
    aliases = alias_literals(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        found |= _node_names(node, aliases)
    return found


def scan_setting_calls(root: Path) -> set[str]:
    """Every literal env read in runtime ``*.py`` under ``root``."""
    found: set[str] = set()
    for tree in runtime_trees(root):
        found.update(_env_names_from_tree(tree))
    return found


def script_reads(root: Path) -> set[str]:
    """Reads in the package's own ``scripts/``: suppress DEAD, never UNDOCUMENTED."""
    scripts = root / "scripts"
    return scan_setting_calls(scripts) if scripts.is_dir() else set()
