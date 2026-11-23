#!/usr/bin/env python3
"""Import the replacement name of every row of the removed-path table.

SCION-CENTRALIZED-CAPABILITIES-R046.3: run in a fresh environment that holds
only the published scion-sdk. A rename that left a file out of the wheel
fails here, before a consumer finds it. The table is the one from
``tests/architecture/removed_paths.toml``. A replacement whose module needs
an optional extra is verified against the installed wheel's file instead:
the module file must ship in the distribution and the name must be defined
at its top level.

Usage: ``python3 scripts/check_published_imports.py [TABLE]``
"""

from __future__ import annotations

import argparse
import ast
import importlib
import importlib.util
import sys
import tomllib
from pathlib import Path

TABLE = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "architecture"
    / "removed_paths.toml"
)


def _top_level_names(path: Path) -> set[str]:
    """The names defined at the top level of the module file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _defined_in_wheel(module: str, name: str | None) -> bool:
    """The module file ships in the installed distribution, and the name,
    when one is named, is defined at its top level."""
    import scion_sdk

    parts = module.split(".")[1:]
    filename = "/".join(parts) + ".py" if parts else "__init__.py"
    path = Path(scion_sdk.__path__[0]) / filename
    if not path.is_file():
        return False
    return name is None or name in _top_level_names(path)


def _imports(new: str) -> bool:
    """Whether the replacement imports, or its file and name ship in the wheel."""
    module, _, name = new.rpartition(".")
    try:
        if importlib.util.find_spec(new) is not None:
            return True
    except ImportError:
        # The parent chain may need an optional extra; fall back to the wheel.
        pass
    if not name:
        return _defined_in_wheel(module, None)
    try:
        target = importlib.import_module(module)
    except ImportError:
        return _defined_in_wheel(module, name)
    return hasattr(target, name)


def check(table: Path = TABLE) -> list[str]:
    """The replacement names that are absent from the installed distribution,
    in table order."""
    with table.open("rb") as raw:
        entries = tomllib.load(raw)["removed"]
    return [entry["new"] for entry in entries if not _imports(entry["new"])]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import every replacement name of the removed-path table."
    )
    parser.add_argument("table", nargs="?", default=TABLE, type=Path)
    args = parser.parse_args(argv)
    missing = check(args.table)
    for name in missing:
        print(f"missing from the installed distribution: {name}")
    if missing:
        return 1
    print(f"every replacement of {args.table.name} is present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
