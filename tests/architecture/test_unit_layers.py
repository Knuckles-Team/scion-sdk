"""The SDK's units follow the declared layer order and form no import cycle.

A unit is one top-level module or package of ``scion_sdk``; ``scion_sdk/layers.toml``
orders the units, most stable first. Every absolute and relative import counts,
including function-local and ``TYPE_CHECKING`` imports: coupling, not load order,
is the question (SCION-CENTRALIZED-CAPABILITIES-R044).
"""

from __future__ import annotations

import ast
import tomllib
from collections import defaultdict
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "scion_sdk"
LAYERS = ROOT / PACKAGE / "layers.toml"
ROOT_UNIT = "__init__"

#: The one cycle left: ``ports.decide_runner`` types its protocol with
#: ``scion_sdk.decide`` values while ``scion_sdk.decide`` implements the port.
#: ``scion_sdk.decide`` is frozen until the owner rules where decisions live;
#: the ruling removes this entry, and the test fails until it is removed.
PENDING_OWNER_RULING = frozenset({frozenset({"decide", "ports"})})


def _modules(package_dir: Path) -> dict[str, tuple[Path, bool]]:
    modules: dict[str, tuple[Path, bool]] = {}
    for path in sorted(package_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = list(path.relative_to(package_dir.parent).with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts.pop()
        modules[".".join(parts)] = (path, is_package)
    return modules


def _unit(module: str) -> str:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else ROOT_UNIT


def _targets(
    module: str, is_package: bool, node: ast.Import | ast.ImportFrom
) -> Iterator[str]:
    if isinstance(node, ast.Import):
        yield from (alias.name for alias in node.names)
        return
    base = node.module or ""
    if node.level:
        anchor = module.split(".") if is_package else module.split(".")[:-1]
        anchor = anchor[: len(anchor) - (node.level - 1)]
        base = ".".join([*anchor, base] if base else anchor)
    for alias in node.names:
        yield f"{base}.{alias.name}"


def _resolve(target: str, modules: Mapping[str, object]) -> str | None:
    if target.split(".")[0] != PACKAGE:
        return None
    while target not in modules and "." in target:
        target = target.rsplit(".", 1)[0]
    return target if target in modules else None


def _cross_unit_imports(package_dir: Path) -> Iterator[tuple[str, str, str]]:
    """``(importer unit, imported module, "file:line")`` for each import across units."""
    modules = _modules(package_dir)
    for module, (path, is_package) in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            for target in _targets(module, is_package, node):
                resolved = _resolve(target, modules)
                if resolved is None or _unit(resolved) == _unit(module):
                    continue
                where = f"{path.relative_to(package_dir.parent)}:{node.lineno}"
                yield _unit(module), resolved, where


def unit_imports(package_dir: Path) -> dict[tuple[str, str], list[str]]:
    """``{(importer unit, imported unit): ["file:line", ...]}`` across units."""
    edges: dict[tuple[str, str], list[str]] = defaultdict(list)
    for importer, module, where in _cross_unit_imports(package_dir):
        edges[(importer, _unit(module))].append(where)
    return edges


def private_reaches(package_dir: Path) -> list[str]:
    """Each import of a leading-underscore module that belongs to another unit."""
    return sorted(
        f"{where}: {importer} imports {module}"
        for importer, module, where in _cross_unit_imports(package_dir)
        if any(
            part.startswith("_") and not part.startswith("__")
            for part in module.split(".")[1:]
        )
    )


def declared_layers(path: Path) -> list[tuple[str, tuple[str, ...]]]:
    """The ``(name, units)`` of each layer in ``layers.toml``, most stable first."""
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    return [(layer["name"], tuple(layer["units"])) for layer in document["layers"]]


def package_units(package_dir: Path) -> set[str]:
    """Every unit the package holds: top-level modules, packages and the root."""
    return {_unit(module) for module in _modules(package_dir)}


def layer_findings(
    layers: list[tuple[str, tuple[str, ...]]],
    units: set[str],
    edges: Mapping[tuple[str, str], list[str]],
) -> list[str]:
    """Each unit declared twice, missing from every layer, unknown, or importing upward."""
    findings: list[str] = []
    rank: dict[str, int] = {}
    for index, (name, members) in enumerate(layers):
        for unit in members:
            if unit in rank:
                findings.append(f"{unit}: declared in more than one layer")
            rank[unit] = index
            if unit not in units:
                findings.append(f"{unit}: layer {name} names a unit the package lacks")
    findings += [f"{unit}: named in no layer" for unit in sorted(units - set(rank))]
    for (importer, imported), where in sorted(edges.items()):
        if rank.get(imported, -1) > rank.get(importer, len(layers)):
            findings.append(
                f"{where[0]}: {importer} ({layers[rank[importer]][0]}) imports "
                f"{imported} ({layers[rank[imported]][0]}), a later layer"
            )
    return findings


def cycle_groups(edges: Mapping[tuple[str, str], list[str]]) -> list[frozenset[str]]:
    """Strongly connected groups of two or more units (Tarjan)."""
    graph: dict[str, set[str]] = defaultdict(set)
    for importer, imported in edges:
        graph[importer].add(imported)
        graph.setdefault(imported, set())
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    groups: list[frozenset[str]] = []

    def visit(unit: str) -> None:
        index[unit] = low[unit] = len(index)
        stack.append(unit)
        for target in sorted(graph[unit]):
            if target not in index:
                visit(target)
                low[unit] = min(low[unit], low[target])
            elif target in stack:
                low[unit] = min(low[unit], index[target])
        if low[unit] == index[unit]:
            group = set()
            while True:
                member = stack.pop()
                group.add(member)
                if member == unit:
                    break
            if len(group) > 1:
                groups.append(frozenset(group))

    for unit in sorted(graph):
        if unit not in index:
            visit(unit)
    return groups


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R044")
def test_unit_imports_follow_layers_and_no_cycle_remains() -> None:
    package_dir = ROOT / PACKAGE
    edges = unit_imports(package_dir)
    assert (
        layer_findings(declared_layers(LAYERS), package_units(package_dir), edges) == []
    )
    assert set(cycle_groups(edges)) == PENDING_OWNER_RULING


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R044")
def test_no_unit_imports_another_units_private_module() -> None:
    assert private_reaches(ROOT / PACKAGE) == []


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R044")
def test_layer_check_names_each_violation(tmp_path: Path) -> None:
    package_dir = tmp_path / PACKAGE
    for relative, text in {
        "__init__.py": "",
        "base.py": "",
        "_hidden.py": "",
        "high/__init__.py": "from scion_sdk import base\nfrom . import inner, _own\n",
        "high/_own.py": "",
        "high/inner.py": "from scion_sdk.low import thing\n",
        "low.py": "from scion_sdk.high import inner\nthing = 1\n",
        "stray.py": "import scion_sdk.base\nfrom scion_sdk import _hidden\n",
    }.items():
        (package_dir / relative).parent.mkdir(parents=True, exist_ok=True)
        (package_dir / relative).write_text(text, encoding="utf-8")
    layers = [("base", ("base", "low", "_hidden")), ("top", ("high", "ghost", "low"))]
    edges = unit_imports(package_dir)

    assert edges[("high", "base")] == ["scion_sdk/high/__init__.py:1"]
    assert ("high", "high") not in edges
    assert layer_findings(layers, package_units(package_dir), edges) == [
        "ghost: layer top names a unit the package lacks",
        "low: declared in more than one layer",
        "__init__: named in no layer",
        "stray: named in no layer",
    ]
    layers = [
        ("base", ("base", "low", "_hidden", "stray", ROOT_UNIT)),
        ("top", ("high",)),
    ]
    assert layer_findings(layers, package_units(package_dir), edges) == [
        "scion_sdk/low.py:1: low (base) imports high (top), a later layer"
    ]
    assert cycle_groups(edges) == [frozenset({"high", "low"})]
    assert private_reaches(package_dir) == [
        "scion_sdk/stray.py:2: stray imports scion_sdk._hidden"
    ]
