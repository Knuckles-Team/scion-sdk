"""Revocation and prune reach the engine through the runner cycle (R805, R805.4, R805.6)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import anyio
import pytest
from runner_support import freshrss_descriptor

from scion_sdk.contracts import ExternalAccess
from scion_sdk.ports.governance_source import GovernanceSource
from scion_sdk.runner.governance import ConnectorGovernance
from scion_sdk.runner.worker import ConnectorWorker
from scion_sdk.source_connectors import (
    HierarchyConnector,
    PermSyncConnector,
    SlimDocument,
    SourceContainer,
)
from scion_sdk.testing.sinks import InMemorySink

START = datetime(2026, 11, 26, 8, 0, tzinfo=UTC)


class _Source(PermSyncConnector, HierarchyConnector):
    source_type = "demo"

    def __init__(self) -> None:
        super().__init__()
        self.readers = ["a@x.test", "b@x.test"]
        self.ids = ["d1", "d2"]

    def slim(self) -> Iterator[SlimDocument]:
        return (SlimDocument(id=i) for i in self.ids)

    def fetch_access(self) -> Iterator[tuple[str, ExternalAccess]]:
        return ((i, ExternalAccess(user_emails=list(self.readers))) for i in self.ids)

    def containers(self) -> Iterator[SourceContainer]:
        yield SourceContainer(source="demo", container_id="root")


class _Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


def _governance(connector: str, source: _Source) -> dict[str, GovernanceSource]:
    return {connector: ConnectorGovernance("demo", source, source)}


def _worker(
    sink: InMemorySink, source: _Source, clock: _Clock, **over: Any
) -> ConnectorWorker:
    descriptor = freshrss_descriptor(**over)
    services = SimpleNamespace(
        settings=SimpleNamespace(backoff_initial_seconds=1.0, backoff_max_seconds=2.0),
        sink=sink,
        health=None,
        governance=_governance(descriptor.connector, source),
    )
    return ConnectorWorker(descriptor, services, anyio.CapacityLimiter(1), clock=clock)  # type: ignore[arg-type]


def _kinds(sink: InMemorySink) -> list[str]:
    return [b.kind.value for b in sink.batches.values()]


@pytest.mark.spec("SCION-SOURCE-INGEST-R805.4")
async def test_runner_schedules_access_enumeration_and_containers() -> None:
    sink, source, clock = InMemorySink(), _Source(), _Clock()
    worker = _worker(sink, source, clock)
    await worker._govern()
    assert _kinds(sink) == ["access", "records", "enumeration", "containers"]
    clock.now = START + timedelta(seconds=299)
    await worker._govern()
    assert len(sink.batches) == 4  # not due again inside the interval
    clock.now = START + timedelta(seconds=3601)
    await worker._govern()
    assert _kinds(sink)[4:] == ["access", "records", "enumeration", "containers"]
    lowered = _worker(InMemorySink(), source, clock, permission_sync_interval_s=300)
    assert lowered._governance.interval_s == 300


@pytest.mark.spec("SCION-SOURCE-INGEST-R805")
@pytest.mark.spec("SCION-SOURCE-INGEST-R805.6")
async def test_revocation_and_prune_against_real_engine() -> None:
    sink, source, clock = InMemorySink(), _Source(), _Clock()
    worker = _worker(sink, source, clock)
    await worker._govern()
    source.readers.remove("b@x.test")
    source.ids.remove("d2")
    clock.now = START + timedelta(hours=2)
    await worker._govern()
    batches = list(sink.batches.values())
    access = [b.extension["access"] for b in batches if b.kind.value == "access"]
    assert [r["read_principals"] for r in access[0]] == [["a@x.test", "b@x.test"]] * 2
    assert [r["read_principals"] for r in access[-1]] == [["a@x.test"]]
    live = [
        [r.record_id for r in b.authoritative_live_ids or []]
        for b in batches
        if b.kind.value == "records"
    ]
    assert live == [["d1", "d2"], ["d1"]]
