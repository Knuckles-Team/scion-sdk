"""Access, group, enumeration and container batches (SCION-SOURCE-INGEST-R805).

The runner maps what a source reports onto the engine's typed batch kinds and
sends them after content, at most once per interval. The engine admits them;
the SDK never decides what a record may read.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import islice
from typing import Any

from medulla.generated.source_ingestion import (
    SourceBatchKind,
    SourceCheckpoint,
    SourceEntityRef,
    SourceIngestionMode,
    SourceIngestionRequest,
)
from pydantic import BaseModel, ConfigDict

from scion_sdk.config import setting
from scion_sdk.contracts import ExternalAccess
from scion_sdk.ports.governance_source import GovernanceSource
from scion_sdk.ports.sink import Sink
from scion_sdk.source_connectors.base import (
    HierarchyConnector,
    PermSyncConnector,
    SourceContainer,
)

__all__ = [
    "ACCESS_MARKINGS_UNSUPPORTED",
    "AccessSnapshot",
    "ConnectorGovernance",
    "GovernanceSchedule",
    "map_access",
    "run_enumeration",
    "sync_governance",
]

ACCESS_MARKINGS_UNSUPPORTED = "ACCESS_MARKINGS_UNSUPPORTED"
ACCESS_BATCH = 1024
GROUP_BATCH = 64
ENUMERATION_BATCH = 16384
CONTAINER_BATCH = 1024
MIN_INTERVAL_S = 300
MAX_INTERVAL_S = 86400
DEFAULT_INTERVAL_S = 3600


class AccessSnapshot(BaseModel):
    """One record's read access; it carries no document body."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    record_ref: str
    access_public: bool
    read_principals: tuple[str, ...]
    read_groups: tuple[str, ...]
    synced_at: str


def map_access(
    source: str, pairs: Iterable[tuple[str, ExternalAccess]], now: datetime
) -> tuple[list[AccessSnapshot], list[str]]:
    """Map ``(document_id, ExternalAccess)`` pairs; return snapshots and unmapped refs.

    A record with markings is not mapped (the engine then fails it closed).
    """
    stamp = now.astimezone(UTC).isoformat()
    snapshots: list[AccessSnapshot] = []
    unsupported: list[str] = []
    for record_ref, access in pairs:
        if access.markings:
            unsupported.append(record_ref)
            continue
        snapshots.append(
            AccessSnapshot(
                source=source,
                record_ref=record_ref,
                access_public=access.is_public,
                read_principals=tuple(access.user_emails),
                read_groups=(
                    *access.group_ids,
                    *(f"role:{role}" for role in access.read_roles),
                ),
                synced_at=stamp,
            )
        )
    return snapshots, unsupported


@dataclass(frozen=True)
class EnumerationOutcome:
    complete: bool
    ids_digest: str
    count: int


@dataclass
class GovernanceReport:
    """Batch kinds sent, in order, and any refusal code."""

    kinds: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)


def _chunks[T](items: Iterable[T], size: int) -> Iterator[list[T]]:
    iterator = iter(items)
    while chunk := list(islice(iterator, size)):
        yield chunk


async def _send(
    sink: Sink,
    connector: str,
    kind: SourceBatchKind,
    *,
    run_id: str,
    seq: int,
    extension: dict[str, list[Any]] | None,
    mode: SourceIngestionMode = SourceIngestionMode.DELTA,
    live: list[str] | None = None,
) -> None:
    stream = f"governance/{kind.value}"
    previous = (await sink.source_status(connector, stream)).accepted_checkpoint
    request = SourceIngestionRequest(
        connector=connector,
        kind=kind,
        mode=mode,
        strict_schema=False,
        records=[],
        extension=extension,
        authoritative_live_ids=(
            [SourceEntityRef(record_id=i, stream=stream) for i in live]
            if live is not None
            else None
        ),
        provider_checkpoint=SourceCheckpoint(
            stream=stream, position={"run_id": run_id, "seq": seq}
        ),
        expected_previous_checkpoint=previous,
    )
    await sink.submit(request)


def _digest(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()


async def run_enumeration(
    sink: Sink, connector: str, ids: Iterable[str], *, run_id: str
) -> EnumerationOutcome:
    """Stream ``ids`` in ``reconcile`` batches, then close the enumeration.

    An iterator that raises closes with ``complete: false`` and the digest of
    the ids so far; an incomplete enumeration never removes anything.
    """
    seen: list[str] = []
    pending: list[str] = []
    complete = True
    seq = 0

    async def flush() -> None:
        nonlocal seq, pending
        if not pending:
            return
        await _send(
            sink,
            connector,
            SourceBatchKind.RECORDS,
            run_id=run_id,
            seq=seq,
            extension=None,
            mode=SourceIngestionMode.RECONCILE,
            live=pending,
        )
        seq += 1
        pending = []

    iterator = iter(ids)
    while True:
        try:
            item = next(iterator)
        except StopIteration:
            break
        except Exception:  # the source iterator broke: close incomplete
            complete = False
            break
        seen.append(item)
        pending.append(item)
        if len(pending) >= ENUMERATION_BATCH:
            await flush()
    await flush()
    digest = _digest(seen)
    await _send(
        sink,
        connector,
        SourceBatchKind.ENUMERATION,
        run_id=run_id,
        seq=seq,
        extension={"enumeration": [{"complete": complete, "ids_digest": digest}]},
    )
    return EnumerationOutcome(complete, digest, len(seen))


class GovernanceSchedule:
    """Runs governance at most once per interval (``SOURCE_PERMISSION_SYNC_INTERVAL_S``)."""

    def __init__(self, descriptor_interval_s: int | None = None) -> None:
        configured = int(
            setting("SOURCE_PERMISSION_SYNC_INTERVAL_S", DEFAULT_INTERVAL_S)
        )
        interval = min(max(configured, MIN_INTERVAL_S), MAX_INTERVAL_S)
        if descriptor_interval_s is not None:
            interval = min(interval, max(descriptor_interval_s, MIN_INTERVAL_S))
        self.interval_s = interval
        self._last: datetime | None = None

    def due(self, now: datetime) -> bool:
        return (
            self._last is None or (now - self._last).total_seconds() >= self.interval_s
        )

    def mark(self, now: datetime) -> None:
        self._last = now


class ConnectorGovernance:
    """Adapts a ``PermSyncConnector`` and a ``HierarchyConnector`` to the port."""

    def __init__(
        self,
        source: str,
        perms: PermSyncConnector,
        hierarchy: HierarchyConnector | None = None,
    ) -> None:
        self.source = source
        self._perms = perms
        self._hierarchy = hierarchy

    def fetch_access(self) -> Iterator[tuple[str, ExternalAccess]]:
        return self._perms.fetch_access()

    def fetch_groups(self) -> Iterator[object]:
        return self._perms.fetch_groups()

    def slim_ids(self) -> Iterator[str]:
        return (document.id for document in self._perms.slim())

    def containers(self) -> Iterator[object]:
        return self._hierarchy.containers() if self._hierarchy else iter(())


async def sync_governance(
    source: GovernanceSource,
    sink: Sink,
    now: datetime,
    schedule: GovernanceSchedule,
    *,
    connector: str,
) -> GovernanceReport:
    """Send access, groups, enumeration and containers, in that order, if due."""
    report = GovernanceReport()
    if not schedule.due(now):
        return report
    schedule.mark(now)
    run_id = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    snapshots, unsupported = map_access(source.source, source.fetch_access(), now)
    if unsupported:
        report.codes.append(ACCESS_MARKINGS_UNSUPPORTED)
    for seq, batch in enumerate(_chunks(snapshots, ACCESS_BATCH)):
        await _send(
            sink,
            connector,
            SourceBatchKind.ACCESS,
            run_id=run_id,
            seq=seq,
            extension={"access": [s.model_dump(mode="json") for s in batch]},
        )
        report.kinds.append("access")
    for seq, group_batch in enumerate(_chunks(source.fetch_groups(), GROUP_BATCH)):
        await _send(
            sink,
            connector,
            SourceBatchKind.GROUPS,
            run_id=run_id,
            seq=seq,
            extension={"groups": [g.model_dump(mode="json") for g in group_batch]},  # type: ignore[attr-defined]
        )
        report.kinds.append("groups")
    await run_enumeration(sink, connector, source.slim_ids(), run_id=run_id)
    report.kinds.append("enumeration")
    containers: list[SourceContainer] = list(source.containers())  # type: ignore[arg-type]
    for seq, container_batch in enumerate(_chunks(containers, CONTAINER_BATCH)):
        await _send(
            sink,
            connector,
            SourceBatchKind.CONTAINERS,
            run_id=run_id,
            seq=seq,
            extension={
                "containers": [c.model_dump(mode="json") for c in container_batch]
            },
        )
        report.kinds.append("containers")
    return report
