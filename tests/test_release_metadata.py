"""Release metadata: the cutover changelog names every removed path.

The 0.10.0 changelog section lists the release 0.10.0 break by kind with
the replacement for each, and the package version always matches the top
changelog section (SCION-CENTRALIZED-CAPABILITIES-R046.2).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
PYPROJECT = ROOT / "pyproject.toml"
TABLE = ROOT / "tests" / "architecture" / "removed_paths.toml"
CUTOVER_VERSION = "0.10.0"


def _changelog_lines() -> list[str]:
    return CHANGELOG.read_text(encoding="utf-8").splitlines()


def _section(version: str) -> str:
    """The changelog section for ``version``: from its heading to the next."""
    lines = _changelog_lines()
    start = next(
        (i for i, line in enumerate(lines) if line.strip() == f"## {version}"),
        None,
    )
    assert start is not None, f"no {version} section in the changelog"
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start + 1 : end])


def _version() -> str:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return project["project"]["version"]


def _table() -> list[dict[str, str]]:
    with TABLE.open("rb") as raw:
        return tomllib.load(raw)["removed"]


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R046.2")
def test_changelog_names_every_removed_path_of_the_table() -> None:
    section = _section(CUTOVER_VERSION)
    for entry in _table():
        old, new, kind = entry["old"], entry["new"], entry["kind"]
        if kind == "module":
            # One line per unit: the old path and its replacement together.
            together = [
                line for line in section.splitlines() if old in line and new in line
            ]
            assert together, f"the changelog does not list {old} -> {new} on one line"
            continue
        old_name, new_name = old.rsplit(".", 1)[1], new.rsplit(".", 1)[1]
        assert old_name in section, f"the changelog does not name {old}"
        assert new_name in section, f"the changelog does not name {new}"
    command = "python -m scion_sdk.testing.surface_draft complete <package>"
    assert command in section, "the changelog does not name the new drafting command"


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R046.2")
def test_version_matches_the_top_changelog_section() -> None:
    headings = [
        line[3:].strip() for line in _changelog_lines() if line.startswith("## ")
    ]
    assert headings, "the changelog has no version section"
    assert headings[0] == _version(), (
        f"changelog top section is {headings[0]!r}, package version is {_version()!r}"
    )
