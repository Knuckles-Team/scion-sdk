"""The batch-migration import scan: AST-based, not a brittle text search."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from scion_sdk.testing.fleet_migration import (
    BATCH_LETTERS,
    PackageScanResult,
    batch_census,
    batch_roots,
    scan_for_meristem_imports,
    scan_many_packages,
)


def _write(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_a_clean_package_is_reported_migrated(tmp_path: Path) -> None:
    _write(
        tmp_path / "clean_pkg" / "server.py",
        "from scion_sdk.mcp.server import build_server\n",
    )

    result = scan_for_meristem_imports(tmp_path / "clean_pkg")

    assert isinstance(result, PackageScanResult)
    assert result.migrated
    assert result.offending_files == ()


def test_a_plain_import_is_caught(tmp_path: Path) -> None:
    _write(tmp_path / "legacy_pkg" / "a.py", "import meristem.core.config\n")

    result = scan_for_meristem_imports(tmp_path / "legacy_pkg")

    assert not result.migrated
    assert result.offending_files == ("a.py",)


def test_a_from_import_is_caught(tmp_path: Path) -> None:
    _write(
        tmp_path / "legacy_pkg" / "b.py",
        "from meristem.mcp.verbose_tools import register_tool_surface\n",
    )

    result = scan_for_meristem_imports(tmp_path / "legacy_pkg")

    assert not result.migrated
    assert result.offending_files == ("b.py",)


def test_a_mention_in_a_string_or_comment_is_not_a_false_positive(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "clean_pkg" / "docs.py",
        '# migrated off meristem\n"""See meristem for history."""\n',
    )

    result = scan_for_meristem_imports(tmp_path / "clean_pkg")

    assert result.migrated


def test_excluded_directories_are_not_scanned(tmp_path: Path) -> None:
    _write(
        tmp_path / "pkg" / ".venv" / "site" / "x.py",
        "import meristem\n",
    )
    _write(tmp_path / "pkg" / "real.py", "import httpx\n")

    result = scan_for_meristem_imports(tmp_path / "pkg")

    assert result.migrated


def test_a_file_that_fails_to_parse_is_skipped_not_fabricated_clean(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "pkg" / "broken.py", "def broken(:\n")

    result = scan_for_meristem_imports(tmp_path / "pkg")

    assert result.offending_files == ()
    assert result.unscanned_files == ("broken.py",)
    assert not result.migrated


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R014.28")
def test_incomplete_or_ambiguous_fleet_scans_never_prove_migration(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    missing = tmp_path / "missing"
    assert not scan_for_meristem_imports(empty).migrated
    assert not scan_for_meristem_imports(missing).migrated

    root = tmp_path / "pkg"
    _write(root / "good.py", "import httpx\n")
    (root / "invalid.py").write_bytes(b"\xff")
    (root / "linked.py").symlink_to(root / "good.py")
    target = tmp_path / "target"
    target.mkdir()
    (root / "linked_dir").symlink_to(target, target_is_directory=True)
    result = scan_for_meristem_imports(root)
    assert not result.migrated
    assert result.unscanned_files == ("invalid.py", "linked.py", "linked_dir")

    other = tmp_path / "other" / "pkg"
    _write(other / "good.py", "import httpx\n")
    with pytest.raises(ValueError, match="unique"):
        scan_many_packages([root, other])


def test_traversal_error_prevents_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "pkg"
    _write(root / "good.py", "import httpx\n")

    def broken_walk(
        path: Path, *, onerror: Callable[[OSError], None]
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        assert path == root
        onerror(PermissionError(13, "denied", str(root / "private")))
        yield str(root), [], ["good.py"]

    monkeypatch.setattr("os.walk", broken_walk)
    result = scan_for_meristem_imports(root)
    assert result.unscanned_files == ("private",)
    assert not result.migrated


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R014.29")
@pytest.mark.parametrize(
    "source",
    [
        '__import__("meristem.core")',
        'import importlib as loader\nloader.import_module("meristem.core")',
        'from importlib import import_module as load\nload("meristem.core")',
        'from builtins import __import__ as load\nload("meristem")',
    ],
)
def test_fleet_scan_detects_literal_dynamic_legacy_imports(
    tmp_path: Path, source: str
) -> None:
    root = tmp_path / "pkg"
    _write(root / "dynamic.py", source)
    result = scan_for_meristem_imports(root)
    assert not result.migrated
    assert result.offending_files == ("dynamic.py",)


def test_fleet_scan_marks_unknown_dynamic_import_incomplete(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    _write(root / "dynamic.py", "import importlib\nimportlib.import_module(name)")
    result = scan_for_meristem_imports(root)
    assert not result.migrated
    assert result.unscanned_files == ("dynamic.py",)
    _write(root / "dynamic.py", 'import importlib\nimportlib.import_module("json")')
    assert scan_for_meristem_imports(root).migrated


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R014.1")
def test_scan_many_packages_reports_each_root_by_name(tmp_path: Path) -> None:
    """SCION-CONNECTOR-CONTROL-R014.1: one call site scans a whole fleet of
    connector package roots and keys each result by package name, so a
    cross-repository census is one function call, not a hand-rolled loop."""
    _write(
        tmp_path / "clean_pkg" / "server.py",
        "from scion_sdk.mcp.server import build_server\n",
    )
    _write(tmp_path / "legacy_pkg" / "a.py", "import meristem.core.config\n")

    results = scan_many_packages([tmp_path / "clean_pkg", tmp_path / "legacy_pkg"])

    assert set(results) == {"clean_pkg", "legacy_pkg"}
    assert results["clean_pkg"].migrated
    assert not results["legacy_pkg"].migrated
    assert results["legacy_pkg"].offending_files == ("a.py",)


_ROWS = ("R009", "R010", "R011", "R012", "R013")


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R009")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R010")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R011")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R012")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R013")
def test_batch_census_is_clean(tmp_path: Path) -> None:
    """Each alphabetical batch is selected from the tree and scanned in one call;
    a legacy import, an unresolved dynamic import and a missing package are all
    reported as not clean."""
    sample = {"R009": "ansible-tower-mcp", "R010": "gitlab-api", "R011": "kafka-mcp"}
    sample |= {"R012": "plane-agent", "R013": "wger-agent"}
    for name in sample.values():
        _write(tmp_path / name / "server.py", "import scion_sdk\n")
    _write(tmp_path / "zeta-mcp" / "a.py", "import meristem.core.config\n")
    _write(
        tmp_path / "tidy-api" / "a.py", "import importlib\nimportlib.import_module(x)"
    )
    (tmp_path / "notes.txt").write_text("not a package", encoding="utf-8")

    for row in _ROWS[:4]:
        results = batch_census(tmp_path, row, expected=[sample[row]])
        assert results[sample[row]].migrated
        assert all(name[0] in BATCH_LETTERS[row] for name in results)
        assert all(r.migrated for r in results.values())

    last = batch_census(tmp_path, "R013", expected=["wger-agent", "weather-gone"])
    assert last["wger-agent"].migrated
    assert not last["zeta-mcp"].migrated
    assert not last["tidy-api"].migrated
    assert not last["weather-gone"].migrated
    assert last["zeta-mcp"].offending_files == ("a.py",)
    assert last["tidy-api"].unscanned_files == ("a.py",)


def test_batch_roots_selects_by_first_letter_and_keeps_expected(tmp_path: Path) -> None:
    for name in ("alpha-mcp", "echo-api", "foxtrot", ".hidden"):
        (tmp_path / name).mkdir()
    (tmp_path / "apple.txt").write_text("file", encoding="utf-8")
    roots = batch_roots(tmp_path, BATCH_LETTERS["R009"], expected=["bravo-mcp"])
    assert [root.name for root in roots] == ["alpha-mcp", "bravo-mcp", "echo-api"]


def _git_init(root: Path) -> str:
    import subprocess

    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
        "PATH": __import__("os").environ["PATH"],
    }
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", "-C", str(root), *a],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    run("init", "-q")
    run("add", "-A")
    run("commit", "-q", "-m", "x")
    return run("rev-parse", "HEAD").stdout.strip()


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R014.27")
def test_census_receipt_names_revisions_and_flags_unresolved(tmp_path: Path) -> None:
    from scion_sdk.testing.fleet_migration import CensusReceipt, fleet_census_receipt

    _write(tmp_path / "clean_pkg" / "server.py", "import scion_sdk\n")
    _write(tmp_path / "legacy_pkg" / "a.py", "import meristem.core.config\n")
    head = _git_init(tmp_path / "clean_pkg")
    _git_init(tmp_path / "legacy_pkg")
    receipts = {
        r.package: r
        for r in fleet_census_receipt(
            [tmp_path / "clean_pkg", tmp_path / "legacy_pkg", tmp_path / "ghost"]
        )
    }
    assert isinstance(receipts["clean_pkg"], CensusReceipt)
    assert receipts["clean_pkg"].clean and receipts["clean_pkg"].revision == head
    assert receipts["clean_pkg"].scanned_files == 1
    assert not receipts["legacy_pkg"].clean
    assert not receipts["ghost"].clean and receipts["ghost"].revision == ""


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R020.1")
def test_loader_inventory_complete_and_parity_rejects_differences(
    tmp_path: Path,
) -> None:
    from scion_sdk.testing.fleet_migration import (
        JsonTagLoaderEntry,
        LoaderInventoryItem,
        OperationMapping,
        loader_parity_problems,
        scan_json_tag_loaders,
    )

    root = tmp_path / "pkg"
    _write(root / "tags.json", '{"tools": []}')
    _write(
        root / "loader.py",
        'import json\nDATA = json.loads(open("tags.json").read())\n',
    )
    _write(root / "plain.py", 'NAME = "tags.json"\n')
    (found,) = scan_json_tag_loaders([root])
    assert type(found).__name__ == LoaderInventoryItem.__name__
    assert (found.loader_path, found.json_path) == ("loader.py", "tags.json")
    assert len(found.json_digest) == 64
    inventory = [(found.package, found.loader_path)]
    assert loader_parity_problems([], inventory) == [
        "loader not inventoried: pkg:loader.py"
    ]
    mapping = OperationMapping(
        "a", "a", "i", "i", "o", "o", frozenset({"t"}), frozenset({"t"})
    )
    entry = JsonTagLoaderEntry(
        "pkg",
        "rev",
        "loader.py",
        "tags.json",
        found.json_digest,
        "operation",
        (mapping,),
    )
    assert loader_parity_problems([entry], inventory) == []
    drifted = OperationMapping("a", "a", "i", "j", "o", "o")
    bad = JsonTagLoaderEntry(
        "pkg",
        "rev",
        "loader.py",
        "tags.json",
        found.json_digest,
        "operation",
        (drifted,),
    )
    assert any(
        "input schema differs" in p for p in loader_parity_problems([bad], inventory)
    )
