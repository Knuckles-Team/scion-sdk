"""The shared static environment scan reads literal names from source (R053.1)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scion_sdk import env_scan


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R053.1")
def test_scan_helpers_read_literal_names_and_assignments(tmp_path: Path) -> None:
    source = tmp_path / "reader.py"
    source.write_text(
        "import os\n"
        "NAME = 'SCION_EXAMPLE'\n"
        "value = os.environ.get(NAME)\n"
        "def f(a, b):\n    return setting('SCION_OTHER', 1)\n"
        "class C:\n    attr = 1\n",
        encoding="utf-8",
    )
    tree = env_scan.parse_python(source)
    assert tree is not None
    assert env_scan.ENV_NAME.match("SCION_EXAMPLE")
    assert env_scan.walk_files(tmp_path, suffix=".py") == [source]
    assert env_scan.skip_runtime_file(tmp_path, source) is False
    assert [t for t in env_scan.runtime_trees(tmp_path)]
    assert env_scan.literal_str(ast.Constant("x")) == "x"
    assert env_scan.literal_env_name(ast.Constant("SCION_EXAMPLE")) == "SCION_EXAMPLE"
    assert env_scan.literal_env_name(ast.Constant("lower")) is None
    assign = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign))
    pairs = env_scan.assign_pairs(assign)
    assert pairs is not None
    assert env_scan.target_name(pairs[0][0]) == "NAME"
    assert env_scan.function_params(tree) == {"f": ["a", "b"]}
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
    assert env_scan.attr_assignments(cls, "attr") == []
    assert env_scan.alias_literals(tree)["NAME"] == "SCION_EXAMPLE"
    environ = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and n.attr == "environ"
    )
    assert env_scan.is_environ(environ)
    assert "SCION_OTHER" in env_scan.scan_setting_calls(tmp_path)
    assert isinstance(env_scan.script_reads(tmp_path), set)
