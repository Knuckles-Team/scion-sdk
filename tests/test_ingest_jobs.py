"""The sync runner submits ingest jobs through the engine queue and backs off (R907)."""

from __future__ import annotations

from typing import Any

import pytest
from test_schema_drift import SCHEMA, SyntheticAdapter, contract

from scion_sdk.runner.ingest_jobs import (
    INGEST_QUEUE_FULL,
    IngestJob,
    IngestQueueUnavailable,
    submit_jobs,
)
from scion_sdk.runner.syncing import SyncTarget, sync_stream
from scion_sdk.testing.results import SessionFactory
from scion_sdk.testing.sinks import InMemorySink


class _Full(RuntimeError):
    code = INGEST_QUEUE_FULL


class _Queue:
    def __init__(self, full_times: int = 0) -> None:
        self.full_times = full_times
        self.enqueued: list[tuple[str, str]] = []
        self.drained: list[str] = []

    async def ingest_enqueue(self, source: str, ref: str) -> None:
        if self.full_times:
            self.full_times -= 1
            raise _Full()
        self.enqueued.append((source, ref))

    async def start_source_drain(self, source: str) -> None:
        self.drained.append(source)


@pytest.mark.spec("SCION-SOURCE-INGEST-R907")
async def test_sync_stream_enqueues_jobs_and_backs_off(
    sessions: SessionFactory,
) -> None:
    queue, pauses = _Queue(full_times=2), []

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)

    jobs = (
        IngestJob("document", "docs", "d1"),
        IngestJob("codebase", "repo", "main"),
        IngestJob("source_drain", "docs"),
    )
    target = SyncTarget(
        "synthetic",
        InMemorySink(),
        1,
        schema_contract=contract(SCHEMA),
        ingest_jobs=jobs,
        ingest_queue=queue,
        sleep=sleep,
    )
    async with sessions() as session:
        await sync_stream(session, SyntheticAdapter(contract(SCHEMA)), target)
    assert queue.enqueued == [("docs", "d1"), ("repo", "main")]
    assert queue.drained == ["docs"]
    assert pauses == [1.0, 2.0]


@pytest.mark.spec("SCION-SOURCE-INGEST-R907")
async def test_full_queue_defers_and_missing_method_refuses() -> None:
    async def sleep(_: float) -> None:
        return None

    jobs = (IngestJob("document", "docs", "d1"),)
    report = await submit_jobs(_Queue(full_times=99), jobs, sleep=sleep, max_pauses=2)
    assert report.deferred == list(jobs) and report.submitted == []
    with pytest.raises(IngestQueueUnavailable) as caught:
        await submit_jobs(object(), jobs, sleep=sleep)
    assert caught.value.method == "ingest_enqueue"
    other: Any = _Queue()

    async def boom(*_: Any) -> None:
        raise ValueError("other")

    other.ingest_enqueue = boom
    with pytest.raises(ValueError):
        await submit_jobs(other, jobs, sleep=sleep)
