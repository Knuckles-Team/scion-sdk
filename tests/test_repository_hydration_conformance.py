"""SCION-REPOSITORY-TRANSPORT-R002: the SDK is the sole path for hydration.

A connector hydrates repository content only through
``scion_sdk.repository.index_repository_snapshot``; the conformance check
refuses the engine operation reached directly, the engine record class
named in connector code, and imports of the SDK's private
``scion_sdk.repository._*`` modules. Local git use for a developer
workspace is not hydration and is not flagged.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from connector_dist import build

from scion_sdk.connector import resolve_connector
from scion_sdk.testing._conformance_static import check_repository_hydration
from scion_sdk.testing.conformance import CHECKS, ConformanceFailure

pytest_plugins = ["pytester"]

GIT_WORKSPACE = """\
import subprocess


def prepare_workspace(root):
    subprocess.run(
        ["git", "clone", "https://git.example.test/acme.git", root],
        check=True,
    )
    subprocess.run(
        ["git", "worktree", "add", str(root / "wt")],
        cwd=root,
        check=True,
    )
"""

SDK_HYDRATION = """\
from scion_sdk.repository import index_repository_snapshot


def hydrate(provider, revision):
    return index_repository_snapshot(provider, revision)
"""


def _install(root: Path, monkeypatch: pytest.MonkeyPatch, **kwargs):
    build(root, **kwargs)
    monkeypatch.syspath_prepend(str(root))
    for name in [m for m in sys.modules if m.split(".")[0] == "democonn"]:
        monkeypatch.delitem(sys.modules, name)
    return resolve_connector("democonn")


@pytest.mark.spec("SCION-REPOSITORY-TRANSPORT-R002")
@pytest.mark.parametrize(
    ("source", "finding"),
    [
        (
            "def hydrate(client):\n    client.index_repository(batch)\n",
            "HYDRATION_OUTSIDE_SDK",
        ),
        (
            "record = IndexRepository(batch)\n",
            "HYDRATION_OUTSIDE_SDK",
        ),
        (
            "from scion_sdk.repository._walk import walk_refs\n",
            "HYDRATION_PRIVATE_IMPORT",
        ),
    ],
)
def test_direct_index_repository_call_fails_the_check(
    tmp_path, monkeypatch, source, finding
) -> None:
    """A leaking fixture fails the check, naming file, line and finding."""
    resolved = _install(tmp_path, monkeypatch, extra_files={"leak.py": source})
    with pytest.raises(ConformanceFailure) as failure:
        check_repository_hydration(resolved)
    assert re.search(rf"leak\.py:\d+: {finding}", str(failure.value))
    for name in [m for m in sys.modules if m.split(".")[0] == "democonn"]:
        sys.modules.pop(name)


@pytest.mark.spec("SCION-REPOSITORY-TRANSPORT-R002")
def test_local_git_and_the_sdk_entry_pass_the_check(tmp_path, monkeypatch) -> None:
    """Local git workspace use and the one SDK entry are not hydration findings."""
    resolved = _install(
        tmp_path,
        monkeypatch,
        extra_files={"workspace.py": GIT_WORKSPACE, "hydrate.py": SDK_HYDRATION},
    )
    check_repository_hydration(resolved)


@pytest.mark.spec("SCION-REPOSITORY-TRANSPORT-R002")
def test_connector_plugin_runs_the_hydration_check(pytester, monkeypatch) -> None:
    """The plugin collects the hydration check for a declared connector."""
    assert any(
        check.name == "repository_hydration"
        and check.spec == "SCION-REPOSITORY-TRANSPORT-R002"
        for check in CHECKS
    )
    build(pytester.path)
    monkeypatch.setenv("PYTHONPATH", str(pytester.path))
    result = pytester.runpytest_subprocess("-q", "--collect-only")
    assert "::connector_conformance::repository_hydration" in result.stdout.str()
