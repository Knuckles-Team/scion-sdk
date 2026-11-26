"""An engine-fired paper-intake job reaches the worker and links its receipt (R005.27, R005.30).

The engine schedule is the engine's; this proof runs the SDK worker against a
structural double of the engine task queue with a controlled clock, so no
wall-clock sleep occurs.
"""

from __future__ import annotations

import pytest
from runner.test_paper_intake import BOUNDARY, DIGEST, _Ledger, _Services

from scion_sdk.runner.errors import PaperIntakeRefusal
from scion_sdk.runner.ingest_worker import run_ingest_worker
from scion_sdk.runner.job_dispatch import (
    PAPER_INTAKE_KIND,
    JobHandler,
    UnknownJobKind,
    dispatch_job,
    paper_intake_handler,
)
from scion_sdk.runner.paper_intake import CYCLE_MS


class _QueueEngine:
    """The engine surface the worker uses: admission, a task queue and acks."""

    def __init__(self, jobs):
        self.jobs = list(jobs)
        self.acks = []
        self.order = []

    def worker_admit(self, cores, memory_bytes, max_workers, lanes_supported):
        return {
            "slots": 1,
            "reserved_lane": "ingest",
            "lease_seconds": 30.0,
            "retry_after_seconds": 0.0,
        }

    def task_queue_claim(self, queue, lease_seconds):
        return self.jobs.pop(0) if self.jobs else None

    def task_queue_ack(self, task_id, outcome, detail=""):
        self.order.append(("ack", task_id))
        self.acks.append((task_id, outcome, detail))


def _job(task_id, run_id, boundary):
    return {
        "task_id": task_id,
        "kind": PAPER_INTAKE_KIND,
        "payload": {
            "run_id": run_id,
            "cycle_boundary_ms": boundary,
            "clock_id": "engine-clock",
            "goals": ["g1"],
            "proposed_digest": DIGEST,
        },
    }


def _drain(engine, handlers: dict[str, JobHandler]):
    count = run_ingest_worker(
        engine,
        dispatch_job(handlers),
        cores=1,
        memory_bytes=1,
        should_stop=lambda: not engine.jobs,
        sleep=lambda _s: None,
    )
    return count


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.27")
def test_scheduler_firing_reaches_worker_and_links_receipt():
    ledger, services = _Ledger(), _Services()
    engine = _QueueEngine([_job("t1", "run-1", BOUNDARY)])
    original = ledger.record

    async def record(receipt, digest):
        engine.order.append(("receipt", receipt.run_id))
        await original(receipt, digest)

    ledger.record = record
    handlers = {
        PAPER_INTAKE_KIND: paper_intake_handler(services, ledger, lambda: BOUNDARY)
    }
    assert _drain(engine, handlers) == 1
    assert engine.acks == [("t1", "succeeded", "")]
    # the receipt is linked to the run id before the ack
    assert engine.order == [("receipt", "run-1"), ("ack", "t1")]
    assert ledger.receipts["run-1"].ingestion_receipt == "ingest-run-1"
    # an unregistered kind is failed to the engine, never guessed
    other = _QueueEngine([{"task_id": "t2", "kind": "unknown", "payload": {}}])
    assert _drain(other, handlers) == 0
    assert other.acks[0][:2] == ("t2", "failed") and "unknown" in other.acks[0][2]
    with pytest.raises(UnknownJobKind):
        dispatch_job(handlers)({"kind": "nope"})


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.30")
def test_six_hour_cycle_admits_and_ingests():
    ledger, services = _Ledger(), _Services()
    now = [BOUNDARY]
    handlers = {
        PAPER_INTAKE_KIND: paper_intake_handler(services, ledger, lambda: now[0])
    }
    first = _QueueEngine([_job("t1", "run-1", BOUNDARY)])
    assert _drain(first, handlers) == 1
    now[0] = BOUNDARY + CYCLE_MS  # the controlled clock reaches the next boundary
    second = _QueueEngine([_job("t2", "run-2", BOUNDARY + CYCLE_MS)])
    assert _drain(second, handlers) == 1
    admitted = ledger.receipts["run-1"]
    assert admitted.ingested_papers == ("p1", "p3")
    assert [e.paper_id for e in admitted.exclusions] == ["p2"]
    # the excluded paper is not proposed again in the next cycle
    assert "p2" not in ledger.receipts["run-2"].ingested_papers
    assert ledger.receipts["run-2"].exclusions == ()
    # a restart that re-delivers run-1 returns its first receipt and ingests nothing new
    calls = len(services.calls)
    again = _QueueEngine([_job("t1b", "run-1", BOUNDARY)])
    assert _drain(again, handlers) == 1
    assert len(services.calls) == calls
    assert ledger.receipts["run-1"] is admitted

    # a denied scheduler is a typed refusal, acked failed, with no effect
    class _Denied(_Services):
        async def search(self, goals):
            raise PermissionError("scheduler")

    denied = {
        PAPER_INTAKE_KIND: paper_intake_handler(_Denied(), _Ledger(), lambda: BOUNDARY)
    }
    refused = _QueueEngine([_job("t3", "run-3", BOUNDARY)])
    assert _drain(refused, denied) == 0
    assert refused.acks[0][:2] == ("t3", "failed")
    assert "SCHEDULER_ACCESS_DENIED" in refused.acks[0][2]
    with pytest.raises(PaperIntakeRefusal):
        dispatch_job(denied)(_job("t4", "run-4", BOUNDARY))
