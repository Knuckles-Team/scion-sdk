"""Bound data for fleet compatibility evidence, without rollout authority."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "MajorVersionRefusal",
    "MajorVersionTransition",
    "PackageCompatibilityReceipt",
    "PackageInventoryEntry",
    "PackageInventorySnapshot",
    "PinChangeGate",
    "RolloutEvidence",
    "RolloutPackageRecord",
    "authorize_major_version_pin_changes",
    "check_receipt_binding",
    "reject_evidence_reuse",
    "require_package_pin_change",
    "rollout_decision",
]

_ID = Field(min_length=1, max_length=512, pattern=r"^\S(?:.*\S)?$")
_SHA256 = Field(pattern=r"^[0-9a-f]{64}$")
_REVISION = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_PIN = Field(pattern=r"^(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){0,2}$")


class _Strict(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class PackageInventoryEntry(_Strict):
    """A package at one immutable source and dependency-file preimage."""

    package_id: str = _ID
    repository_id: str = _ID
    source_commit: str = _REVISION
    current_pin: str = _PIN
    preimage_digest: str = _SHA256


class PackageInventorySnapshot(_Strict):
    """A sorted, unique inventory and the SHA-256 of its canonical entries."""

    entries: tuple[PackageInventoryEntry, ...] = Field(min_length=1)
    inventory_digest: str = _SHA256

    @model_validator(mode="after")
    def _validate_entries(self) -> PackageInventorySnapshot:
        package_ids = [entry.package_id for entry in self.entries]
        if package_ids != sorted(set(package_ids)):
            raise ValueError("inventory package IDs must be unique and sorted")
        payload = [entry.model_dump(mode="json") for entry in self.entries]
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() != self.inventory_digest:
            raise ValueError("inventory digest does not match entries")
        return self


class MajorVersionTransition(_Strict):
    """The exact artifact, pins and inventory of one major upgrade."""

    transition_id: str = _ID
    distribution: str = _ID
    old_pin: str = _PIN
    target_pin: str = _PIN
    target_artifact_digest: str = _SHA256
    inventory_digest: str = _SHA256

    @model_validator(mode="after")
    def _major_upgrade(self) -> MajorVersionTransition:
        if int(self.target_pin.split(".")[0]) <= int(self.old_pin.split(".")[0]):
            raise ValueError("target pin must advance the major version")
        return self


class PackageCompatibilityReceipt(_Strict):
    """One completed suite result bound to a package and transition."""

    transition_id: str = _ID
    inventory_digest: str = _SHA256
    package_id: str = _ID
    repository_id: str = _ID
    source_commit: str = _REVISION
    old_pin: str = _PIN
    target_pin: str = _PIN
    preimage_digest: str = _SHA256
    target_artifact_digest: str = _SHA256
    suite: str = _ID
    run: str = _ID
    completed_at: datetime
    outcome: Literal["passed", "failed", "incomplete", "cancelled"]

    @model_validator(mode="after")
    def _aware_completion(self) -> PackageCompatibilityReceipt:
        if self.completed_at.utcoffset() is None:
            raise ValueError("completion timestamp must include a timezone")
        return self


def check_receipt_binding(
    transition: MajorVersionTransition,
    inventory: PackageInventorySnapshot,
    receipt: PackageCompatibilityReceipt,
) -> None:
    """Refuse a receipt that does not match its transition and inventory entry.

    This validates only binding. It does not prove suite execution, inventory
    completeness, or authorize any package write.
    """
    if transition.inventory_digest != inventory.inventory_digest:
        raise ValueError("transition inventory mismatch")
    entries = {entry.package_id: entry for entry in inventory.entries}
    entry = entries.get(receipt.package_id)
    if entry is None:
        raise ValueError("receipt package not in inventory")
    actual = (
        receipt.transition_id,
        receipt.inventory_digest,
        receipt.repository_id,
        receipt.source_commit,
        receipt.old_pin,
        receipt.target_pin,
        receipt.preimage_digest,
        receipt.target_artifact_digest,
    )
    expected = (
        transition.transition_id,
        transition.inventory_digest,
        entry.repository_id,
        entry.source_commit,
        transition.old_pin,
        transition.target_pin,
        entry.preimage_digest,
        transition.target_artifact_digest,
    )
    if actual != expected or entry.current_pin != transition.old_pin:
        raise ValueError("receipt binding mismatch")


#: R007.4: every code a pin-change refusal carries.
REFUSAL_CODES = frozenset(
    {
        "RECEIPT_MISSING",
        "RECEIPT_DUPLICATE",
        "RECEIPT_EXTRA",
        "RECEIPT_FAILED",
        "RECEIPT_INCOMPLETE",
        "RECEIPT_CANCELLED",
        "RECEIPT_STALE",
        "RECEIPT_MISMATCH",
        "PREIMAGE_CHANGED",
        "EVIDENCE_REUSE",
    }
)

#: A receipt older than this at gate time is stale and cannot authorize a write.
DEFAULT_RECEIPT_MAX_AGE = timedelta(hours=24)


class MajorVersionRefusal(ValueError):
    """A refusal that carries one stable code and names the package it concerns."""

    def __init__(self, code: str, package_id: str = "", detail: str = "") -> None:
        if code not in REFUSAL_CODES:
            raise ValueError(f"unknown refusal code {code!r}")
        self.code = code
        self.package_id = package_id
        super().__init__(f"{code}: {package_id} {detail}".rstrip())


class PinChangeGate:
    """The fleet-wide admission for one transition: one use per package preimage."""

    def __init__(
        self,
        transition: MajorVersionTransition,
        authorized: Mapping[str, tuple[PackageCompatibilityReceipt, str]],
    ) -> None:
        self.transition = transition
        self._authorized = dict(authorized)
        self._consumed: set[str] = set()

    @property
    def package_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._authorized))

    def receipt_for(self, package_id: str) -> PackageCompatibilityReceipt:
        return self._authorized[package_id][0]


_OUTCOME_REFUSAL = {
    "failed": "RECEIPT_FAILED",
    "incomplete": "RECEIPT_INCOMPLETE",
    "cancelled": "RECEIPT_CANCELLED",
}


def authorize_major_version_pin_changes(
    transition: MajorVersionTransition,
    inventory: PackageInventorySnapshot,
    receipts: Iterable[PackageCompatibilityReceipt],
    *,
    now: datetime,
    max_age: timedelta = DEFAULT_RECEIPT_MAX_AGE,
) -> PinChangeGate:
    """Admit the whole fleet or refuse before any package is written.

    Every inventoried package needs exactly one passing, fresh receipt bound to
    this transition, artifact, inventory, source, pins and preimage.
    """
    if transition.inventory_digest != inventory.inventory_digest:
        raise MajorVersionRefusal("RECEIPT_MISMATCH", detail="transition inventory")
    by_package: dict[str, list[PackageCompatibilityReceipt]] = {}
    for receipt in receipts:
        by_package.setdefault(receipt.package_id, []).append(receipt)
    known = {entry.package_id for entry in inventory.entries}
    for extra in sorted(set(by_package) - known):
        raise MajorVersionRefusal("RECEIPT_EXTRA", extra)
    authorized: dict[str, tuple[PackageCompatibilityReceipt, str]] = {}
    for entry in inventory.entries:
        found = by_package.get(entry.package_id, [])
        if not found:
            raise MajorVersionRefusal("RECEIPT_MISSING", entry.package_id)
        if len(found) > 1:
            raise MajorVersionRefusal("RECEIPT_DUPLICATE", entry.package_id)
        receipt = found[0]
        try:
            check_receipt_binding(transition, inventory, receipt)
        except ValueError as error:
            raise MajorVersionRefusal("RECEIPT_MISMATCH", entry.package_id) from error
        if receipt.outcome != "passed":
            raise MajorVersionRefusal(
                _OUTCOME_REFUSAL[receipt.outcome], entry.package_id
            )
        if now - receipt.completed_at > max_age:
            raise MajorVersionRefusal("RECEIPT_STALE", entry.package_id)
        authorized[entry.package_id] = (receipt, entry.preimage_digest)
    return PinChangeGate(transition, authorized)


def require_package_pin_change(
    gate: PinChangeGate, package_id: str, pin_preimage: str
) -> None:
    """A writer's check before its first pin write; each package passes it once.

    ``pin_preimage`` is the SHA-256 of the dependency file as the writer reads it
    now. A changed file, an unadmitted package or a reused authorization refuses.
    """
    entry = gate._authorized.get(package_id)
    if entry is None:
        raise MajorVersionRefusal("RECEIPT_MISSING", package_id)
    if package_id in gate._consumed:
        raise MajorVersionRefusal("EVIDENCE_REUSE", package_id, "already consumed")
    if pin_preimage != entry[1]:
        raise MajorVersionRefusal("PREIMAGE_CHANGED", package_id)
    gate._consumed.add(package_id)


class RolloutPackageRecord(_Strict):
    """One package's gate decision and writer outcome."""

    package_id: str = _ID
    repository_id: str = _ID
    source_commit: str = _REVISION
    preimage_digest: str = _SHA256
    old_pin: str = _PIN
    target_pin: str = _PIN
    target_artifact_digest: str = _SHA256
    suite: str = _ID
    run: str = _ID
    receipt_outcome: Literal["passed", "failed", "incomplete", "cancelled"]
    gate_decision: Literal["authorized", "refused"]
    writer_outcome: Literal["applied", "refused", "not_attempted"]
    refusal_code: str = ""


class RolloutEvidence(_Strict):
    """Stored proof of one rollout; it holds digests and ids, never credentials."""

    transition_id: str = _ID
    inventory_digest: str = _SHA256
    members: tuple[str, ...]
    records: tuple[RolloutPackageRecord, ...]
    state: Literal["complete", "refused", "reconciliation_required"]

    @model_validator(mode="after")
    def _consistent(self) -> RolloutEvidence:
        if tuple(r.package_id for r in self.records) != self.members:
            raise ValueError("records must list exactly the members, in order")
        if self.state != rollout_decision(self):
            raise ValueError("state does not follow from the records")
        return self


def rollout_decision(evidence: RolloutEvidence) -> str:
    """Rebuild the rollout state from the per-package records alone."""
    outcomes = {record.writer_outcome for record in evidence.records}
    if outcomes == {"applied"}:
        return "complete"
    if "applied" in outcomes:
        return "reconciliation_required"
    return "refused"


def reject_evidence_reuse(
    evidence: RolloutEvidence, transition_id: str, package_id: str
) -> RolloutPackageRecord:
    """Return the record for this package, refusing evidence of another transition."""
    if evidence.transition_id != transition_id:
        raise MajorVersionRefusal("EVIDENCE_REUSE", package_id, "other transition")
    for record in evidence.records:
        if record.package_id == package_id:
            return record
    raise MajorVersionRefusal("EVIDENCE_REUSE", package_id, "other package")
