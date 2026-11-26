"""Scheduled paper intake: models, the cycle, refusals and evidence."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scion_sdk.runner.errors import PaperIntakeRefusal
from scion_sdk.runner.paper_intake import (
    CYCLE_MS,
    AdmissionDecision,
    IntakeLedger,
    PaperIntakeJob,
    PaperIntakeReceipt,
    PaperIntakeServices,
    goal_set_digest,
    run_paper_intake,
)

BOUNDARY = 10 * CYCLE_MS
DIGEST = "a" * 64


def _job(run_id="run-1", boundary=BOUNDARY, goals=("g1", "g2")):
    return PaperIntakeJob(
        run_id=run_id,
        cycle_boundary_ms=boundary,
        clock_id="engine-clock",
        goals=goals,
        proposed_digest=DIGEST,
    )


class _Ledger:
    def __init__(self):
        self.receipts = {}
        self.excluded_by_goals = {}

    async def receipt(self, run_id):
        return self.receipts.get(run_id)

    async def excluded(self, goal_digest):
        return frozenset(self.excluded_by_goals.get(goal_digest, ()))

    async def record(self, receipt, goal_digest):
        self.receipts[receipt.run_id] = receipt
        self.excluded_by_goals.setdefault(goal_digest, set()).update(
            e.paper_id for e in receipt.exclusions
        )


class _Services:
    def __init__(self, papers=("p1", "p2", "p3"), admit=("p1", "p3"), threshold=0.6):
        self.papers, self.admit, self.threshold = papers, admit, threshold
        self.calls = []
        self.decision_missing = False

    async def search(self, goals):
        self.calls.append("search")
        return self.papers

    async def propose(self, run_id, paper_ids):
        self.calls.append("propose")

    async def decide(self, run_id, paper_ids):
        self.calls.append("decide")
        if self.decision_missing:
            return None
        return AdmissionDecision(
            record_id=f"decide-{run_id}",
            scores={p: 0.9 if p in self.admit else 0.1 for p in paper_ids},
            admitted={p: p in self.admit for p in paper_ids},
            threshold=self.threshold,
        )

    async def ingest(self, run_id, paper_ids):
        self.calls.append("ingest")
        return f"ingest-{run_id}"


def _typed(ledger: IntakeLedger, services: PaperIntakeServices):
    return ledger, services


def _now(value=BOUNDARY):
    return lambda: value


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.25")
def test_job_and_receipt_models_are_strict():
    job = _job()
    assert job.cycle_boundary_ms % CYCLE_MS == 0
    with pytest.raises(ValidationError):
        PaperIntakeJob(**{**job.model_dump(), "proposed_digest": "short"})
    with pytest.raises(ValidationError):
        PaperIntakeJob(**{**job.model_dump(), "goals": ()})
    with pytest.raises(ValidationError):
        PaperIntakeJob(**{**job.model_dump(), "extra": 1})
    receipt = PaperIntakeReceipt(
        run_id="r",
        cycle_boundary_ms=BOUNDARY,
        proposed_digest=DIGEST,
        goal_set_digest=goal_set_digest(["g"]),
        ingested_papers=("p1",),
        admission_receipt="d",
        ingestion_receipt="i",
        accepted_count=1,
    )
    with pytest.raises(ValidationError, match="accepted count"):
        receipt.model_validate({**receipt.model_dump(), "accepted_count": 2})
    with pytest.raises(ValidationError, match="both ingested and excluded"):
        receipt.model_validate(
            {
                **receipt.model_dump(),
                "exclusions": [{"paper_id": "p1", "reason": "x"}],
            }
        )
    assert goal_set_digest(["b", "a"]) == goal_set_digest(["a", "b", "a"])


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.26")
async def test_cycle_dedup_and_restart_boundary():
    ledger, services = _typed(_Ledger(), _Services())
    receipt = await run_paper_intake(
        _job(), services=services, ledger=ledger, now_ms=_now()
    )
    assert services.calls == ["search", "propose", "decide", "ingest"]
    assert receipt.ingested_papers == ("p1", "p3")
    assert [e.paper_id for e in receipt.exclusions] == ["p2"]
    # A retry of the same run returns the first receipt without another call.
    again = await run_paper_intake(
        _job(), services=services, ledger=ledger, now_ms=_now()
    )
    assert again == receipt and services.calls.count("decide") == 1
    # The next cycle does not propose an excluded paper again for the same goals.
    next_services = _Services()
    second = await run_paper_intake(
        _job("run-2", BOUNDARY + CYCLE_MS),
        services=next_services,
        ledger=ledger,
        now_ms=_now(BOUNDARY + CYCLE_MS),
    )
    assert "p2" not in second.ingested_papers
    assert all(e.paper_id != "p2" for e in second.exclusions)


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.28")
async def test_boundary_and_producer_failures_are_typed():
    ledger = _Ledger()

    async def code(job, services, now=BOUNDARY):
        with pytest.raises(PaperIntakeRefusal) as refused:
            await run_paper_intake(
                job, services=services, ledger=ledger, now_ms=_now(now)
            )
        return refused.value.code

    assert await code(_job(boundary=BOUNDARY + 1), _Services()) == "CLOCK_MISALIGNED"
    assert (
        await code(_job(), _Services(), now=BOUNDARY + 2 * CYCLE_MS)
        == "JOB_OUT_OF_WINDOW"
    )
    missing = _Services()
    missing.decision_missing = True
    assert await code(_job("a"), missing) == "ADMISSION_UNAVAILABLE"
    assert "ingest" not in missing.calls
    assert (
        await code(_job("b"), _Services(threshold=None)) == "ADMISSION_THRESHOLD_ABSENT"
    )

    class _Partial(_Services):
        async def decide(self, run_id, paper_ids):
            return AdmissionDecision(
                record_id="d", scores={}, admitted={}, threshold=0.6
            )

    assert await code(_job("c"), _Partial()) == "RELEVANCE_SCORES_MISSING"
    assert ledger.receipts == {}  # a refused run never advances the job
    with pytest.raises(ValueError, match="unknown paper intake refusal"):
        PaperIntakeRefusal("NOT_A_CODE")
    assert (
        PaperIntakeRefusal("SCHEDULER_ACCESS_DENIED").code == "SCHEDULER_ACCESS_DENIED"
    )


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.29")
async def test_receipt_identity_is_stable_and_redacted():
    ledger = _Ledger()
    first = await run_paper_intake(
        _job(), services=_Services(), ledger=ledger, now_ms=_now()
    )
    other = await run_paper_intake(
        _job("run-1"), services=_Services(), ledger=_Ledger(), now_ms=_now()
    )
    assert first == other
    assert first.goal_set_digest == goal_set_digest(["g1", "g2"])
    assert (
        len(first.proposed_digest) == 64 and first.admission_receipt == "decide-run-1"
    )
    assert first.accepted_count == 2
    dumped = first.model_dump_json()
    for forbidden in ("secret", "token", "abstract", "title"):
        assert forbidden not in dumped
