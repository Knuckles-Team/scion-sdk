"""The ``connector-rollout`` console script and ``run_rollout`` (SCION-CONNECTOR-CONTROL-R007.3).

``connector-rollout --plan FILE`` reads a plan (transition, inventory, the declared
package ids, each package's dependency file, and the suite command), runs each
package's own suite, admits the fleet through one gate, and only then writes each
package's pin. Exit ``0``: complete. ``2``: refused before any write. ``3``: some
packages written and some refused, so the fleet needs reconciliation (no rollback).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess  # nosec B404
import sys
from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from scion_sdk.testing.fleet_migration import (
    InventoryIncomplete,
    SuiteResult,
    SuiteRunner,
    evaluate_major_version_compatibility,
)
from scion_sdk.testing.major_version import (
    MajorVersionRefusal,
    MajorVersionTransition,
    PackageInventoryEntry,
    PackageInventorySnapshot,
    RolloutEvidence,
    RolloutPackageRecord,
    authorize_major_version_pin_changes,
    require_package_pin_change,
)

__all__ = ["command_suite_runner", "main", "run_rollout"]

#: Why a package suite is bounded: one hung suite must not stall the fleet rollout.
SUITE_TIMEOUT_S = 1800


def command_suite_runner(
    argv: Sequence[str],
    *,
    clock: Callable[[], datetime],
    timeout_s: float = SUITE_TIMEOUT_S,
) -> SuiteRunner:
    """Run ``argv`` in the isolated candidate directory; exit 0 means passed."""

    def run(
        entry: PackageInventoryEntry,
        transition: MajorVersionTransition,
        candidate: Path,
    ) -> SuiteResult:
        env = {
            "PACKAGE_ID": entry.package_id,
            "SOURCE_COMMIT": entry.source_commit,
            "TARGET_PIN": transition.target_pin,
            "TARGET_ARTIFACT_DIGEST": transition.target_artifact_digest,
        }
        try:
            done = subprocess.run(  # nosec B603
                list(argv),
                cwd=candidate,
                env=env,
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
            outcome = "passed" if done.returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            outcome = "incomplete"
        except OSError:
            outcome = "failed"
        return SuiteResult(
            suite=Path(argv[0]).name or "suite",
            run=f"{entry.package_id}@{entry.source_commit[:12]}",
            completed_at=clock(),
            outcome=outcome,  # type: ignore[arg-type]
        )

    return run


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_rollout(
    inventory: PackageInventorySnapshot,
    transition: MajorVersionTransition,
    *,
    declared_package_ids: Collection[str] | None,
    suite_runner: SuiteRunner,
    pin_files: Mapping[str, Path],
    rewrite_pin: Callable[[str], str],
    clock: Callable[[], datetime],
    after_write: Callable[[str], None] | None = None,
) -> RolloutEvidence:
    """Producer, then the fleet gate, then each writer; a refusal precedes every write.

    A fleet-level refusal raises before any file is touched. After admission each
    package's writer rechecks its own preimage; a refusal there leaves earlier
    packages applied and the evidence says ``reconciliation_required``.
    """
    receipts = evaluate_major_version_compatibility(
        inventory,
        transition,
        declared_package_ids=declared_package_ids,
        suite_runner=suite_runner,
    )
    gate = authorize_major_version_pin_changes(
        transition, inventory, receipts, now=clock()
    )
    by_package = {receipt.package_id: receipt for receipt in receipts}
    records: list[RolloutPackageRecord] = []
    for entry in inventory.entries:
        receipt = by_package[entry.package_id]
        path = pin_files[entry.package_id]
        outcome, code = "applied", ""
        try:
            require_package_pin_change(gate, entry.package_id, _digest(path))
            path.write_text(
                rewrite_pin(path.read_text(encoding="utf-8")), encoding="utf-8"
            )
        except MajorVersionRefusal as refusal:
            outcome, code = "refused", refusal.code
        records.append(
            RolloutPackageRecord(
                package_id=entry.package_id,
                repository_id=entry.repository_id,
                source_commit=entry.source_commit,
                preimage_digest=entry.preimage_digest,
                old_pin=transition.old_pin,
                target_pin=transition.target_pin,
                target_artifact_digest=transition.target_artifact_digest,
                suite=receipt.suite,
                run=receipt.run,
                receipt_outcome=receipt.outcome,
                gate_decision="authorized" if outcome == "applied" else "refused",
                writer_outcome=outcome,  # type: ignore[arg-type]
                refusal_code=code,
            )
        )
        if outcome == "applied" and after_write is not None:
            after_write(entry.package_id)
    members = tuple(record.package_id for record in records)
    applied = {r.package_id for r in records if r.writer_outcome == "applied"}
    state = (
        "complete"
        if len(applied) == len(records)
        else "reconciliation_required"
        if applied
        else "refused"
    )
    return RolloutEvidence(
        transition_id=transition.transition_id,
        inventory_digest=inventory.inventory_digest,
        members=members,
        records=tuple(records),
        state=state,  # type: ignore[arg-type]
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="connector-rollout")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, help="write the evidence here")
    args = parser.parse_args(argv)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    transition = MajorVersionTransition.model_validate_json(
        json.dumps(plan["transition"])
    )
    inventory = PackageInventorySnapshot.model_validate_json(
        json.dumps(plan["inventory"])
    )

    def clock() -> datetime:
        return datetime.now(UTC)

    try:
        evidence = run_rollout(
            inventory,
            transition,
            declared_package_ids=plan.get("declared_package_ids"),
            suite_runner=command_suite_runner(
                plan["suite_command"],
                clock=clock,
                timeout_s=plan.get("suite_timeout_s", SUITE_TIMEOUT_S),
            ),
            pin_files={k: Path(v) for k, v in plan["pin_files"].items()},
            rewrite_pin=lambda text: text.replace(
                transition.old_pin, transition.target_pin
            ),
            clock=clock,
        )
    except (InventoryIncomplete, MajorVersionRefusal) as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    if args.evidence:
        args.evidence.write_text(evidence.model_dump_json(), encoding="utf-8")
    return 0 if evidence.state == "complete" else 3


if __name__ == "__main__":
    raise SystemExit(main())
