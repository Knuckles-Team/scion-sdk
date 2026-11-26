"""A three-package fixture fleet for the major-version rollout tests."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scion_sdk.testing.fleet_migration import SuiteResult
from scion_sdk.testing.major_version import (
    MajorVersionTransition,
    PackageInventoryEntry,
    PackageInventorySnapshot,
)

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
ARTIFACT = hashlib.sha256(b"scion-sdk-4.0.0").hexdigest()
PACKAGES = ("alpha-agent", "beta-agent", "gamma-agent")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_fleet(
    root: Path,
) -> tuple[PackageInventorySnapshot, MajorVersionTransition, dict[str, Path]]:
    files: dict[str, Path] = {}
    entries = []
    for index, name in enumerate(PACKAGES):
        path = root / f"{name}.toml"
        path.write_bytes(f'dependencies = ["scion-sdk>=3.3,<4"]\n# {name}\n'.encode())
        files[name] = path
        entries.append(
            PackageInventoryEntry(
                package_id=name,
                repository_id=f"org/{name}",
                source_commit=f"{index + 1:040x}",
                current_pin="3.3",
                preimage_digest=sha(path.read_bytes()),
            )
        )
    payload = [e.model_dump(mode="json") for e in entries]
    digest = sha(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    )
    inventory = PackageInventorySnapshot(
        entries=tuple(entries), inventory_digest=digest
    )
    transition = MajorVersionTransition(
        transition_id="t-4",
        distribution="scion-sdk",
        old_pin="3.3",
        target_pin="4.0",
        target_artifact_digest=ARTIFACT,
        inventory_digest=digest,
    )
    return inventory, transition, files


def passing_runner(seen: list[Path] | None = None, outcome: str = "passed"):
    def run(entry, transition, candidate: Path) -> SuiteResult:
        assert candidate.is_dir()
        if seen is not None:
            seen.append(candidate)
        return SuiteResult(
            suite="own-tests",
            run=f"run-{entry.package_id}",
            completed_at=NOW - timedelta(minutes=5),
            outcome=outcome,  # type: ignore[arg-type]
        )

    return run


def rewrite(text: str) -> str:
    return text.replace("3.3,<4", "4.0,<5")
