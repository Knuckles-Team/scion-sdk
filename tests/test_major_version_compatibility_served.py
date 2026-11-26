"""R007 and R007.3/.6: the real rollout command and writers over a three-package fleet."""

from __future__ import annotations

import json
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from major_version_fixtures import NOW, PACKAGES, build_fleet, passing_runner, rewrite

import scion_sdk.testing.rollout as rollout
from scion_sdk.testing.major_version import RolloutEvidence


def _sub(root: Path) -> Path:
    (root / "again").mkdir()
    return root / "again"


def _plan(tmp_path: Path, suite: list[str]) -> tuple[Path, dict[str, Path]]:
    inventory, transition, files = build_fleet(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "transition": transition.model_dump(mode="json"),
                "inventory": inventory.model_dump(mode="json"),
                "declared_package_ids": list(PACKAGES),
                "pin_files": {k: str(v) for k, v in files.items()},
                "suite_command": suite,
                "suite_timeout_s": 60,
            }
        )
    )
    return plan, files


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R007.3")
def test_rollout_command_and_every_writer_call_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, files = _plan(tmp_path, [sys.executable, "-c", "raise SystemExit(0)"])
    evidence_path = tmp_path / "evidence.json"
    done = subprocess.run(  # nosec B603
        [
            sys.executable,
            "-m",
            "scion_sdk.testing.rollout",
            "--plan",
            str(plan),
            "--evidence",
            str(evidence_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert all(">=4.0" in p.read_text() for p in files.values())
    assert (
        RolloutEvidence.model_validate_json(evidence_path.read_text()).state
        == "complete"
    )

    # every writer goes through the gate: count the calls on the real entry point
    calls: list[str] = []
    real = rollout.require_package_pin_change
    monkeypatch.setattr(
        rollout,
        "require_package_pin_change",
        lambda gate, package, digest: (
            calls.append(package),
            real(gate, package, digest),
        )[1],
    )
    inventory, transition, files2 = build_fleet(_sub(tmp_path))
    rollout.run_rollout(
        inventory,
        transition,
        declared_package_ids=PACKAGES,
        suite_runner=passing_runner(),
        pin_files=files2,
        rewrite_pin=rewrite,
        clock=lambda: NOW,
    )
    assert calls == list(PACKAGES)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R007")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R007.6")
def test_fleet_preflight_refusal_preserves_all_pins(tmp_path: Path) -> None:
    plan, files = _plan(tmp_path, [sys.executable, "-c", "raise SystemExit(1)"])
    before = {n: p.read_bytes() for n, p in files.items()}
    done = subprocess.run(  # nosec B603
        [sys.executable, "-m", "scion_sdk.testing.rollout", "--plan", str(plan)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 2 and "RECEIPT_FAILED" in done.stderr
    assert {n: p.read_bytes() for n, p in files.items()} == before


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R007.6")
def test_writer_b_preimage_race_reports_partial_rollout(tmp_path: Path) -> None:
    inventory, transition, files = build_fleet(tmp_path)

    def race(package_id: str) -> None:
        if package_id == "alpha-agent":
            files["beta-agent"].write_text("# raced\n")

    evidence = rollout.run_rollout(
        inventory,
        transition,
        declared_package_ids=PACKAGES,
        suite_runner=passing_runner(),
        pin_files=files,
        rewrite_pin=rewrite,
        clock=lambda: NOW,
        after_write=race,
    )
    applied = [r.package_id for r in evidence.records if r.writer_outcome == "applied"]
    refused = [r.package_id for r in evidence.records if r.writer_outcome == "refused"]
    assert applied == ["alpha-agent", "gamma-agent"] or applied == ["alpha-agent"]
    assert refused == ["beta-agent"]
    assert evidence.state == "reconciliation_required"
