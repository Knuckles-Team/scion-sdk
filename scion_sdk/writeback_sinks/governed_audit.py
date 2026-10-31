"""Durable file-backed audit reservation for governed write-back."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from scion_sdk.file_lock import lock_exclusive, unlock
from scion_sdk.ports.audit_reservation import AuditReservation
from scion_sdk.writeback.errors import AuditReservationUnavailableError

if TYPE_CHECKING:
    from epistemic_graph.generated.write_back import SourceChangeSet

__all__ = ["FileAuditReservation"]


def _write_json_durably(path: Path, payload: dict[str, str]) -> None:
    temporary_fd, temporary_name = tempfile.mkstemp(dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(temporary_fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    if os.name != "posix":
        return
    directory_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


class FileAuditReservation:
    """Persist one audit reservation beside the change set's durable ledger."""

    def __init__(self, directory: Path) -> None:
        self._path = directory / "audit_reservation.json"

    async def reserve(self, change_set: SourceChangeSet) -> AuditReservation:
        """Return the existing reservation, or write a new one atomically."""
        return await asyncio.to_thread(self._reserve, change_set)

    def _reserve(self, change_set: SourceChangeSet) -> AuditReservation:
        reservation_id = (
            f"audit:{change_set.idempotency_key}:{change_set.change_set_digest}"
        )
        reservation = AuditReservation(
            reservation_id, change_set.idempotency_key, change_set.change_set_digest
        )
        try:
            lock_fd = os.open(
                self._path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600
            )
        except OSError as exc:
            raise AuditReservationUnavailableError(
                "audit reservation is unavailable"
            ) from exc
        try:
            lock_exclusive(lock_fd)
            self._verify_or_store(reservation_id)
            _fsync_directory(self._path.parent)
            return reservation
        except (OSError, ValueError, TypeError) as exc:
            raise AuditReservationUnavailableError(
                "audit reservation is unavailable"
            ) from exc
        finally:
            unlock(lock_fd)
            os.close(lock_fd)

    def _verify_or_store(self, reservation_id: str) -> None:
        if not self._path.exists():
            _write_json_durably(self._path, {"reservation_id": reservation_id})
            return
        with self._path.open(encoding="utf-8") as handle:
            stored = json.load(handle)
            os.fsync(handle.fileno())
        if stored != {"reservation_id": reservation_id}:
            raise AuditReservationUnavailableError("audit reservation binding mismatch")
