"""R007.2, R007.4, R007.5: producer, gate refusals and stored evidence."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from major_version_fixtures import (
    NOW,
    PACKAGES,
    build_fleet,
    passing_runner,
    rewrite,
    sha,
)

from scion_sdk.testing.fleet_migration import (
    InventoryIncomplete,
    evaluate_major_version_compatibility,
)
from scion_sdk.testing.major_version import (
    MajorVersionRefusal,
    PinChangeGate,
    RolloutEvidence,
    RolloutPackageRecord,
    authorize_major_version_pin_changes,
    reject_evidence_reuse,
    require_package_pin_change,
    rollout_decision,
)
from scion_sdk.testing.rollout import command_suite_runner, run_rollout


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R007.2")
def test_one_receipt_per_package_bound_to_target_and_files_unchanged(
    tmp_path: Path,
) -> None:
    inventory, transition, files = build_fleet(tmp_path)
    before = {n: p.read_bytes() for n, p in files.items()}
    seen: list[Path] = []
    receipts = evaluate_major_version_compatibility(
        inventory,
        transition,
        declared_package_ids=PACKAGES,
        suite_runner=passing_runner(seen),
    )
    assert [r.package_id for r in receipts] == list(PACKAGES)
    assert {r.target_artifact_digest for r in receipts} == {
        transition.target_artifact_digest
    }
    assert {n: p.read_bytes() for n, p in files.items()} == before
    assert len(set(seen)) == 3 and not any(p.exists() for p in seen)
    for declared in (None, PACKAGES[:2]):
        with pytest.raises(InventoryIncomplete) as refused:
            evaluate_major_version_compatibility(
                inventory,
                transition,
                declared_package_ids=declared,
                suite_runner=passing_runner(),
            )
        assert refused.value.code == "INVENTORY_INCOMPLETE"


def _receipts(tmp_path: Path):
    inventory, transition, files = build_fleet(tmp_path)
    receipts = list(
        evaluate_major_version_compatibility(
            inventory,
            transition,
            declared_package_ids=PACKAGES,
            suite_runner=passing_runner(),
        )
    )
    return inventory, transition, files, receipts


def _refusal(transition, inventory, receipts, **kw) -> str:
    with pytest.raises(MajorVersionRefusal) as refused:
        authorize_major_version_pin_changes(
            transition, inventory, receipts, now=NOW, **kw
        )
    return refused.value.code


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R007.4")
def test_preflight_refusals_and_post_admission_race(tmp_path: Path) -> None:
    inventory, transition, files, receipts = _receipts(tmp_path)
    before = {n: p.read_bytes() for n, p in files.items()}
    first = receipts[0]
    cases = {
        "RECEIPT_MISSING": receipts[1:],
        "RECEIPT_DUPLICATE": [*receipts, first],
        "RECEIPT_EXTRA": [*receipts, first.model_copy(update={"package_id": "zeta"})],
        "RECEIPT_FAILED": [
            first.model_copy(update={"outcome": "failed"}),
            *receipts[1:],
        ],
        "RECEIPT_INCOMPLETE": [
            first.model_copy(update={"outcome": "incomplete"}),
            *receipts[1:],
        ],
        "RECEIPT_CANCELLED": [
            first.model_copy(update={"outcome": "cancelled"}),
            *receipts[1:],
        ],
        "RECEIPT_MISMATCH": [
            first.model_copy(update={"target_artifact_digest": sha(b"other")}),
            *receipts[1:],
        ],
    }
    for code, given in cases.items():
        assert _refusal(transition, inventory, given) == code
    assert _refusal(transition, inventory, receipts, max_age=timedelta(minutes=1)) == (
        "RECEIPT_STALE"
    )
    assert {n: p.read_bytes() for n, p in files.items()} == before

    # Post-admission race: B's file changes after A writes; B refuses, A stays applied.
    def race(package_id: str) -> None:
        if package_id == "alpha-agent":
            files["beta-agent"].write_text("# edited concurrently\n")

    evidence = run_rollout(
        inventory,
        transition,
        declared_package_ids=PACKAGES,
        suite_runner=passing_runner(),
        pin_files=files,
        rewrite_pin=rewrite,
        clock=lambda: NOW,
        after_write=race,
    )
    outcomes = {r.package_id: r.writer_outcome for r in evidence.records}
    assert outcomes["alpha-agent"] == "applied"
    assert outcomes["beta-agent"] == "refused"
    assert evidence.records[1].refusal_code == "PREIMAGE_CHANGED"
    assert evidence.state == "reconciliation_required"
    assert files["beta-agent"].read_text() == "# edited concurrently\n"


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R007.5")
def test_decisions_rebuild_from_stored_evidence_and_reuse_refused(
    tmp_path: Path,
) -> None:
    inventory, transition, files, receipts = _receipts(tmp_path)
    evidence = run_rollout(
        inventory,
        transition,
        declared_package_ids=PACKAGES,
        suite_runner=passing_runner(),
        pin_files=files,
        rewrite_pin=rewrite,
        clock=lambda: NOW,
    )
    stored = evidence.model_dump_json()
    rebuilt = RolloutEvidence.model_validate_json(stored)
    assert rebuilt == evidence and rollout_decision(rebuilt) == "complete"
    assert all(len(r.preimage_digest) == 64 for r in rebuilt.records)
    tampered = stored.replace('"complete"', '"refused"')
    with pytest.raises(ValueError, match="state does not follow"):
        RolloutEvidence.model_validate_json(tampered)
    assert (
        reject_evidence_reuse(rebuilt, "t-4", "beta-agent").package_id == "beta-agent"
    )
    with pytest.raises(MajorVersionRefusal) as other:
        reject_evidence_reuse(rebuilt, "t-5", "beta-agent")
    assert other.value.code == "EVIDENCE_REUSE"
    gate = authorize_major_version_pin_changes(transition, inventory, receipts, now=NOW)
    assert isinstance(gate, PinChangeGate)
    assert isinstance(rebuilt.records[0], RolloutPackageRecord)
    digest = inventory.entries[0].preimage_digest
    require_package_pin_change(gate, "alpha-agent", digest)
    with pytest.raises(MajorVersionRefusal) as again:
        require_package_pin_change(gate, "alpha-agent", digest)
    assert again.value.code == "EVIDENCE_REUSE"


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R007.2")
def test_command_suite_runner_maps_exit_status_and_timeout(tmp_path: Path) -> None:
    import sys

    inventory, transition, _ = build_fleet(tmp_path)
    entry = inventory.entries[0]

    def outcome(code: str, timeout: float = 30) -> str:
        run = command_suite_runner(
            [sys.executable, "-c", code], clock=lambda: NOW, timeout_s=timeout
        )
        return run(entry, transition, tmp_path).outcome

    assert outcome("raise SystemExit(0)") == "passed"
    assert outcome("raise SystemExit(3)") == "failed"
    assert outcome("import time; time.sleep(30)", timeout=0.5) == "incomplete"
