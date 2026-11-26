"""Static census of the SDK's environment reads (SCION-CONNECTOR-CONTROL-R053.2).

Walks the package source, never imports it and reads no live value. Literal names and
module constants resolve; any other name is an unresolved read the export must bind or
refuse. Inherited-environment forwarding (the whole environment handed to a child) is
reported as its own kind.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from scion_sdk.mcp._env_drift_reads import _alias_literals, _is_environ
from scion_sdk.mcp._env_drift_scan import (
    _literal_env_name,
    _parse,
    _skip_runtime_file,
    _walk_files,
)

__all__ = ["EnvironmentRead", "census"]

_READER_FUNCTIONS = frozenset({"_setting", "setting", "getenv"})


@dataclass(frozen=True)
class EnvironmentRead:
    """One environment read or forwarding site found in source."""

    name: str | None  # None when the name is not a literal or module constant
    path: str  # package-relative, forward slashes
    line: int
    kind: str  # "read" or "forward"
    expression: str = ""  # source of an unresolved name, so bindings survive line drift

    @property
    def call_site(self) -> str:
        return f"{self.path}:{self.line}"


def _reader_arg(node: ast.Call) -> ast.expr | None:
    func = node.func
    named = isinstance(func, ast.Name) and func.id in _READER_FUNCTIONS
    os_getenv = (
        isinstance(func, ast.Attribute)
        and func.attr == "getenv"
        and isinstance(func.value, ast.Name)
        and func.value.id == "os"
    )
    environ_get = (
        isinstance(func, ast.Attribute)
        and func.attr in {"get", "pop", "setdefault"}
        and _is_environ(func.value)
    )
    if (named or os_getenv or environ_get) and node.args:
        return node.args[0]
    return None


def _resolve(node: ast.expr, aliases: dict[str, str]) -> str | None:
    literal = _literal_env_name(node)
    if literal:
        return literal
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    if isinstance(node, ast.Attribute):
        return aliases.get(node.attr)
    return None


def _read(
    arg: ast.expr, aliases: dict[str, str], path: str, line: int
) -> EnvironmentRead:
    name = _resolve(arg, aliases)
    return EnvironmentRead(name, path, line, "read", "" if name else ast.unparse(arg))


def _is_forward(node: ast.AST) -> bool:
    """The whole environment handed on: ``dict(os.environ)``, ``**os.environ``, copy()."""
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "copy":
            return _is_environ(func.value)
        if isinstance(func, ast.Name) and func.id == "dict" and node.args:
            return _is_environ(node.args[0])
    if isinstance(node, ast.Dict):
        return any(
            k is None and _is_environ(v)
            for k, v in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.keyword) and node.arg == "env":
        return _is_environ(node.value)
    return False


def _reads_in(tree: ast.AST, path: str) -> list[EnvironmentRead]:
    aliases = _alias_literals(tree)
    found: list[EnvironmentRead] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Call):
            arg = _reader_arg(node)
            if arg is not None:
                found.append(_read(arg, aliases, path, line))
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Load)
            and _is_environ(node.value)
        ):
            found.append(_read(node.slice, aliases, path, line))
        if _is_forward(node):
            found.append(EnvironmentRead(None, path, line, "forward"))
    return found


def census(root: Path | None = None) -> tuple[EnvironmentRead, ...]:
    """Every read and forwarding site under ``root`` (default: this package), sorted."""
    package = root or Path(__file__).resolve().parent
    out: list[EnvironmentRead] = []
    for py in _walk_files(package, suffix=".py"):
        if _skip_runtime_file(package, py):
            continue
        tree = _parse(py)
        if tree is not None:
            rel = f"{package.name}/{py.relative_to(package).as_posix()}"
            out.extend(_reads_in(tree, rel))
    return tuple(sorted(out, key=lambda r: (r.path, r.line, r.name or "")))
