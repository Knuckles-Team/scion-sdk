"""SCION-SOURCE-INGEST-R010: the ingest worker runner."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from scion_sdk.runner.ingest_worker import (
    IngestWorkerEngine,
    WorkerGrant,
    WorkerStopped,
    run_ingest_worker,
)

GRANT = {
    "slots": 1,
    "reserved_lane": "general",
    "lease_seconds": 30,
    "retry_after_seconds": 1,
}


class FakeEngine:
    def __init__(
        self, jobs: list[dict[str, Any]], admit: dict[str, Any] | None = None
    ) -> None:
        self.jobs, self.admit, self.events = jobs, GRANT if admit is None else admit, []

    def worker_admit(
        self,
        cores: int,
        memory_bytes: int,
        max_workers: int | None,
        lanes_supported: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        return self.admit

    def task_queue_claim(
        self, queue: str, lease_seconds: float
    ) -> dict[str, Any] | None:
        self.events.append(("claim", queue, lease_seconds))
        return self.jobs.pop(0) if self.jobs else None

    def task_queue_ack(self, task_id: str, outcome: str, detail: str = "") -> None:
        self.events.append(("ack", task_id, outcome))


@pytest.mark.spec("SCION-SOURCE-INGEST-R010")
def test_ack_follows_execution_and_killed_body_sends_no_ack() -> None:
    engine = FakeEngine([{"task_id": "a"}, {"task_id": "b"}])
    ran: list[str] = []
    stops = iter([False, False, True])

    def body(job: dict[str, Any]) -> None:
        ran.append(job["task_id"])
        engine.events.append(("run", job["task_id"]))
        if job["task_id"] == "b":
            raise KeyboardInterrupt  # a killed body is not an ordinary failure

    with pytest.raises(KeyboardInterrupt):
        run_ingest_worker(
            engine,
            body,
            cores=4,
            memory_bytes=1,
            should_stop=lambda: next(stops),
            sleep=lambda s: None,
        )
    kinds = [e[:2] if e[0] == "run" else e[:1] + e[1:2] for e in engine.events]
    assert ("run", "a") in engine.events and ("ack", "a", "succeeded") in engine.events
    assert engine.events.index(("run", "a")) < engine.events.index(
        ("ack", "a", "succeeded")
    )
    assert not any(e[0] == "ack" and e[1] == "b" for e in engine.events)
    assert kinds and ran == ["a", "b"]


@pytest.mark.spec("SCION-SOURCE-INGEST-R010")
def test_refused_admission_or_absent_floor_stops_without_choosing() -> None:
    refused = FakeEngine([], {"refused": "full"})
    with pytest.raises(WorkerStopped):
        run_ingest_worker(refused, lambda j: None, cores=1, memory_bytes=1)
    absent = FakeEngine([], {"slots": 1})
    with pytest.raises(WorkerStopped):
        run_ingest_worker(absent, lambda j: None, cores=1, memory_bytes=1)
    assert refused.events == [] and absent.events == []


@pytest.mark.spec("SCION-SOURCE-INGEST-R010")
def test_fake_engine_satisfies_protocol_and_grant_is_typed() -> None:
    engine: IngestWorkerEngine = FakeEngine([])
    grant = WorkerGrant(slots=1, lane="g", lease_seconds=1.0, retry_after_seconds=1.0)
    assert engine.worker_admit(1, 1, None, ()) == GRANT and grant.slots == 1


class _SlotEngine:
    """A thread-safe fake engine that grants a fixed number of slots."""

    def __init__(self, slots: int, jobs: int) -> None:
        self.lock = threading.Lock()
        self.jobs = [{"task_id": f"t{i}"} for i in range(jobs)]
        self.admitted: tuple[Any, ...] | None = None
        self.acks: list[tuple[str, str]] = []
        self.grant = {**GRANT, "slots": slots}

    def worker_admit(
        self,
        cores: int,
        memory_bytes: int,
        max_workers: int | None,
        lanes_supported: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        self.admitted = (cores, memory_bytes, max_workers, lanes_supported)
        return self.grant

    def task_queue_claim(
        self, queue: str, lease_seconds: float
    ) -> dict[str, Any] | None:
        with self.lock:
            return self.jobs.pop(0) if self.jobs else None

    def task_queue_ack(self, task_id: str, outcome: str, detail: str = "") -> None:
        with self.lock:
            self.acks.append((task_id, outcome))


@pytest.mark.spec("SCION-SOURCE-INGEST-R011.1")
def test_fake_engine_grants_three_slots_on_sixteen_cores_and_worker_runs_three() -> (
    None
):
    engine = _SlotEngine(slots=3, jobs=3)
    meeting = threading.Barrier(3, timeout=10)
    seen: set[str] = set()

    def body(job: dict[str, Any]) -> None:
        seen.add(threading.current_thread().name)
        meeting.wait()  # returns only when three bodies run at the same time

    stop_after = threading.Event()

    def should_stop() -> bool:
        return len(engine.acks) >= 3 or stop_after.is_set()

    done = run_ingest_worker(
        engine,
        body,
        cores=16,
        memory_bytes=1,
        max_workers=64,
        lanes_supported=("general",),
        should_stop=should_stop,
        sleep=lambda s: None,
    )
    assert engine.admitted == (16, 1, 64, ("general",))
    assert done == 3 and len(seen) == 3
    assert sorted(engine.acks) == [(f"t{i}", "succeeded") for i in range(3)]


@pytest.mark.spec("SCION-SOURCE-INGEST-R011.1")
def test_a_killed_body_stops_every_slot_and_sends_no_ack_for_it() -> None:
    engine = _SlotEngine(slots=2, jobs=1)

    def body(job: dict[str, Any]) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_ingest_worker(engine, body, cores=4, memory_bytes=1, sleep=lambda s: None)
    assert engine.acks == []
