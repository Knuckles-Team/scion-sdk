"""The removed-path table pins the release 0.10.0 hard cutover.

``removed_paths.toml`` holds one entry per row of the plan's removed-path
map: a module move, a function or argument change, and the
surface-drafting command. The cutover leaves no alias: every old path is
absent, every new path imports, nothing in the package still names an old
path, and git rename history backs the table
(SCION-CENTRALIZED-CAPABILITIES-R046.1). The last test proves the
published-import gate, which the release workflow runs against the
installed distribution (SCION-CENTRALIZED-CAPABILITIES-R046.3).
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import importlib.util
import inspect
import io
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "scion_sdk"
TABLE = ROOT / "tests" / "architecture" / "removed_paths.toml"
RENAME_FLAG = "-M50"


def _table() -> list[dict[str, str]]:
    with TABLE.open("rb") as raw:
        entries = tomllib.load(raw)["removed"]
    assert entries, "the removed-path table is empty"
    return entries


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _dotted(path: str) -> str:
    dotted = path[: -len(".py")] if path.endswith(".py") else path
    if dotted.endswith("/__init__"):
        dotted = dotted[: -len("/__init__")]
    return dotted.replace("/", ".")


def _renames(revision: str) -> list[tuple[str, str]]:
    """``(old path, new path)`` for every rename git reports under ``scion_sdk/``."""
    out = _git(
        "log", RENAME_FLAG, "--name-status", "--format=", *revision, "--", "scion_sdk/"
    )
    pairs = []
    for line in out.splitlines():
        if line.startswith("R") and line.count("\t") == 2:
            _, old, new = line.split("\t")
            pairs.append((old, new))
    return pairs


def _rename_pairs() -> dict[str, set[str]]:
    pairs: dict[str, set[str]] = {}
    for old, new in _renames([]):
        pairs.setdefault(old, set()).add(new)
    return pairs


def _reaches(pairs: dict[str, set[str]], old: str, new: str) -> bool:
    """Whether ``new`` follows ``old`` through the rename history."""
    stack, seen = [old], set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for nxt in pairs.get(current, ()):
            if nxt == new:
                return True
            stack.append(nxt)
    return False


def _former_module(old: str) -> tuple[str, str]:
    """The ``(module, name)`` an old name lived in; ``name`` is ``""`` when the
    old path names the module itself."""
    module, _, name = old.rpartition(".")
    if module.count(".") < 2:
        return old, ""
    return module, name


def _assert_replacement_imports(new: str, function_form: bool) -> None:
    if not function_form:
        assert importlib.import_module(new) is not None, f"{new} does not import"
        return
    module, _, name = new.rpartition(".")
    assert hasattr(importlib.import_module(module), name), f"{new} is missing"


def _assert_command_form(new_module: str, subcommand: str) -> None:
    """The replacement command still documents its new form, with the subcommand."""
    module = importlib.import_module(new_module)
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        code = module.main([])
    usage = buffer.getvalue()
    assert code != 0, "the replacement command accepted no arguments"
    assert new_module in usage, f"usage does not name the command: {usage!r}"
    assert subcommand in usage, f"usage does not name {subcommand!r}: {usage!r}"


def _assert_parameter(new: str, argument: str) -> None:
    module, _, name = new.rpartition(".")
    target = importlib.import_module(module)
    assert hasattr(target, name), f"{new} is missing"
    parameters = inspect.signature(getattr(target, name)).parameters
    assert argument in parameters, f"{new} takes no {argument!r} parameter"


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R046.1")
def test_every_removed_path_is_absent_and_its_replacement_imports() -> None:
    for entry in _table():
        old, new, kind = entry["old"], entry["new"], entry["kind"]
        if kind == "module":
            assert importlib.util.find_spec(old) is None, f"{old} still exists"
            _assert_replacement_imports(new, False)
            continue
        former, old_name = _former_module(old)
        if old_name:
            # The former module still ships; the old name is gone from it.
            assert importlib.util.find_spec(former) is not None, f"{former} moved too"
            assert not hasattr(importlib.import_module(former), old_name), (
                f"{old} is still reachable"
            )
        else:
            # The old path names the module itself (the command row).
            assert importlib.util.find_spec(old) is None, f"{old} still exists"
        _assert_replacement_imports(new, bool(old_name))
        if kind == "argument":
            if old_name:
                _assert_parameter(new, entry["argument"])
            else:
                _assert_command_form(new, entry["argument"])


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R046.1")
def test_no_alias_or_reexport_of_a_removed_name() -> None:
    findings = list(_alias_findings())
    assert not findings, "removed names still surface in scion_sdk/: " + ", ".join(
        findings
    )


def _alias_findings() -> Iterator[str]:
    """Where a removed name still reaches the public surface.

    A re-export is an import of an old module path or of an old function
    name; an alias is a module-level binding of an old function name; a
    string naming an old dotted path would re-publish it through config
    or entry points.
    """
    entries = _table()
    old_modules = {
        entry["old"] for entry in entries if _former_module(entry["old"])[1] == ""
    }
    old_functions = {
        name for _, name in (_former_module(entry["old"]) for entry in entries) if name
    }
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        where = path.relative_to(ROOT)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in old_modules:
                        yield f"{where}:{node.lineno} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if not node.level and node.module in old_modules:
                    yield f"{where}:{node.lineno} imports from {node.module}"
                for alias in node.names:
                    if alias.name in old_functions:
                        yield f"{where}:{node.lineno} re-exports old name {alias.name}"
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in old_modules
            ):
                yield f"{where}:{node.lineno} names the removed path {node.value}"
        yield from _module_level_bindings(tree, old_functions, where)


def _module_level_bindings(
    tree: ast.Module, old_functions: set[str], where: Path
) -> Iterator[str]:
    for node in tree.body:
        if isinstance(node, ast.Import):
            bound = {alias.asname or alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            bound = {alias.asname or alias.name for alias in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound = {node.name}
        elif isinstance(node, ast.Assign):
            bound = {item.id for item in node.targets if isinstance(item, ast.Name)}
        elif isinstance(node, ast.AnnAssign):
            bound = {node.target.id} if isinstance(node.target, ast.Name) else set()
        else:
            bound = set()
        for name in sorted(bound & old_functions):
            yield f"{where}:{node.lineno} aliases old name {name}"


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R046.1")
def test_table_covers_every_renamed_file_since_the_last_tag() -> None:
    """Every consumer-visible rename since the last tag is in the table, and
    every module row of the table is a rename the history shows."""
    tags = _git("tag", "--sort=-creatordate").split()
    assert tags, "no release tag to measure renames against"
    last_tag = tags[0]
    table_pairs = {(entry["old"], entry["new"]) for entry in _table()}
    for old, new in _renames([f"{last_tag}..HEAD"]):
        old_dotted, new_dotted = _dotted(old), _dotted(new)
        if old_dotted == new_dotted:
            continue  # the same import path; nothing was removed
        assert (old_dotted, new_dotted) in table_pairs, (
            f"{old_dotted} -> {new_dotted} since {last_tag} is not in the table"
        )
    pairs = _rename_pairs()
    for entry in _table():
        if entry["kind"] != "module":
            continue
        old_file = entry["old"].replace(".", "/") + ".py"
        new_file = entry["new"].replace(".", "/") + ".py"
        assert _reaches(pairs, old_file, new_file), (
            f"history shows no rename {entry['old']} -> {entry['new']}"
        )


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R046.3")
def test_import_check_script_fails_on_a_missing_replacement(tmp_path: Path) -> None:
    """The published-import gate names a missing replacement and exits nonzero;
    the real table imports cleanly in the installed package."""
    script = ROOT / "scripts" / "check_published_imports.py"
    assert script.exists(), "scripts/check_published_imports.py is missing"
    table = tmp_path / "removed_paths.toml"
    table.write_text(
        "[[removed]]\n"
        'old = "scion_sdk.fake_old"\n'
        'new = "scion_sdk.does_not_import_xyz"\n'
        'kind = "module"\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(script), str(table)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "scion_sdk.does_not_import_xyz" in result.stdout
    ok = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
