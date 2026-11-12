"""The release installs the candidate wheel into pinned consumers before upload.

SCION-QUALITY-RELEASE-R007 (see specs/scion-quality-release/requirements.md).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.spec("SCION-QUALITY-RELEASE-R007")

ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "scripts" / "check_release_consumers.py"
    spec = importlib.util.spec_from_file_location("check_release_consumers_ut", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_config_names_arbora_and_a_connector() -> None:
    gate = _load()
    names = {c.name for c in gate.load_consumers()}
    assert "arbora" in names
    assert len(names) >= 2


def test_plan_overrides_the_sdk_with_the_candidate_and_collects_tests(tmp_path) -> None:
    gate = _load()
    consumer = gate.Consumer("Org/conn", "main", "conn", ("test",), "tests")
    steps = [cmd for cmd, _ in gate.plan(consumer, tmp_path / "w.whl", tmp_path)]
    assert any("--override" in cmd for cmd in steps)
    assert steps[-2][-2:] == ["-c", "import conn"]
    assert "--collect-only" in steps[-1]


def test_the_override_keeps_the_extras_the_consumer_declares(tmp_path) -> None:
    gate = _load()
    consumer = gate.Consumer("Org/conn", "main", "conn", (), "tests")
    src = tmp_path / "conn"
    src.mkdir()
    (src / "pyproject.toml").write_text(
        '[project]\nname = "conn"\ndependencies = ["scion-sdk[graphql,mcp]>=0.10"]\n'
        '[project.optional-dependencies]\ntest = ["scion-sdk[requests]>=0.10"]\n',
        encoding="utf-8",
    )
    wheel = tmp_path / "w.whl"
    wheel.write_bytes(b"")
    gate.write_override(consumer, wheel, tmp_path)
    text = (tmp_path / "conn.override.txt").read_text(encoding="utf-8")
    assert text.startswith("scion-sdk[graphql,mcp,requests] @ file://")


def test_a_failing_consumer_step_fails_the_gate(tmp_path) -> None:
    gate = _load()
    wheel = tmp_path / "w.whl"
    wheel.write_bytes(b"")
    consumers = [
        gate.Consumer("Org/good", "main", "good", (), "tests"),
        gate.Consumer("Org/bad", "main", "bad", (), "tests"),
    ]

    def run(command, cwd):
        # the import step of the bad consumer fails, as after a removed path
        return 1 if command[-1] == "import bad" else 0

    failures = gate.check_all(consumers, wheel, tmp_path, run)
    assert set(failures) == {"bad"}
    assert failures["bad"].endswith("import bad")


def test_all_passing_consumers_pass(tmp_path) -> None:
    gate = _load()
    wheel = tmp_path / "w.whl"
    wheel.write_bytes(b"")
    consumers = [gate.Consumer("Org/a", "main", "a", (), "tests")]
    assert gate.check_all(consumers, wheel, tmp_path, lambda c, d: 0) == {}


def test_publish_waits_for_the_consumer_gate() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
    jobs = workflow["jobs"]
    assert "consumer-gate" in jobs["publish-pypi"]["needs"]
    steps = "\n".join(str(s.get("run", "")) for s in jobs["consumer-gate"]["steps"])
    assert "scripts/check_release_consumers.py" in steps
