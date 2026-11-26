"""Scheduled paper intake: validate, search, propose, admit, ingest (SCION-SOURCE-INGEST-R005.25).

The engine owns the schedule and the admission decision. This module holds no period
constant beyond validating alignment and no admission threshold: it receives scores
and the admit flag from the engine decision and fails ``ADMISSION_UNAVAILABLE`` when
there is none.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scion_sdk.runner.errors import PaperIntakeRefusal

__all__ = [
    "CYCLE_MS",
    "AdmissionDecision",
    "IntakeLedger",
    "PaperIntakeJob",
    "PaperIntakeReceipt",
    "PaperIntakeServices",
    "goal_set_digest",
    "run_paper_intake",
]

CYCLE_MS = 21_600_000  # one six-hour cycle; alignment is validated, never scheduled
Identity = Annotated[str, Field(min_length=1, max_length=512, pattern=r"^\S(?:.*\S)?$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def goal_set_digest(goals: Sequence[str]) -> str:
    """SHA-256 over the sorted goal ids."""
    payload = json.dumps(sorted(set(goals)), separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class PaperIntakeJob(_Model):
    """One scheduled cycle the engine fired for this worker."""

    run_id: Identity
    cycle_boundary_ms: int = Field(ge=0)
    clock_id: Identity
    goals: tuple[Identity, ...] = Field(min_length=1, max_length=1000, strict=False)
    proposed_digest: Digest


class Exclusion(_Model):
    """A paper the engine decision did not admit."""

    paper_id: Identity
    reason: Identity


class PaperIntakeReceipt(_Model):
    """Evidence of one run; it carries ids and digests, never claim bodies."""

    run_id: Identity
    cycle_boundary_ms: int = Field(ge=0)
    proposed_digest: Digest
    goal_set_digest: Digest
    ingested_papers: tuple[Identity, ...] = Field(default=(), strict=False)
    exclusions: tuple[Exclusion, ...] = Field(default=(), strict=False)
    admission_receipt: Identity
    ingestion_receipt: Identity
    accepted_count: int = Field(ge=0)

    @model_validator(mode="after")
    def bind_counts(self) -> PaperIntakeReceipt:
        if self.accepted_count != len(self.ingested_papers):
            raise ValueError("accepted count does not bind the ingested papers")
        if set(self.ingested_papers) & {e.paper_id for e in self.exclusions}:
            raise ValueError("a paper is both ingested and excluded")
        return self


class AdmissionDecision(_Model):
    """The engine's ``Decide`` answer: record id, scores and the admit flags."""

    record_id: Identity
    scores: Mapping[str, float] = Field(strict=False)
    admitted: Mapping[str, bool] = Field(strict=False)
    threshold: float | None = Field(default=None, ge=0, le=1)


class IntakeLedger(Protocol):
    """Durable state in the graph: receipts by run and exclusions by goal-set digest."""

    async def receipt(self, run_id: str) -> PaperIntakeReceipt | None: ...

    async def excluded(self, _goal_digest: str) -> frozenset[str]: ...

    async def record(self, receipt: PaperIntakeReceipt, _goal_digest: str) -> None: ...


class PaperIntakeServices(Protocol):
    """The calls a run makes; the engine decision is the only admission authority."""

    async def search(self, goals: Sequence[str]) -> Sequence[str]: ...

    async def propose(self, run_id: str, paper_ids: Sequence[str]) -> None: ...

    async def decide(
        self, run_id: str, paper_ids: Sequence[str]
    ) -> AdmissionDecision | None: ...

    async def ingest(self, run_id: str, paper_ids: Sequence[str]) -> str: ...


async def _check_window(job: PaperIntakeJob, now_ms: int) -> None:
    if job.cycle_boundary_ms % CYCLE_MS:
        raise PaperIntakeRefusal("CLOCK_MISALIGNED")
    if abs(job.cycle_boundary_ms - now_ms) > CYCLE_MS:
        raise PaperIntakeRefusal("JOB_OUT_OF_WINDOW")


def _proposed(paper_ids: Sequence[str]) -> str:
    return hashlib.sha256(
        json.dumps(sorted(paper_ids), separators=(",", ":")).encode()
    ).hexdigest()


async def run_paper_intake(
    job: PaperIntakeJob,
    *,
    services: PaperIntakeServices,
    ledger: IntakeLedger,
    now_ms: Callable[[], int],
) -> PaperIntakeReceipt:
    """Run one cycle; a retry of the same ``run_id`` returns its first receipt.

    Raises:
        PaperIntakeRefusal: the closed refusal codes of R005.28. A failed proposal
            or a missing decision never advances the job.
    """
    prior = await ledger.receipt(job.run_id)
    if prior is not None:
        return prior
    await _check_window(job, now_ms())
    digest = goal_set_digest(job.goals)
    excluded = await ledger.excluded(digest)
    candidates = [p for p in await services.search(job.goals) if p not in excluded]
    await services.propose(job.run_id, candidates)
    decision = await services.decide(job.run_id, candidates)
    if decision is None:
        raise PaperIntakeRefusal("ADMISSION_UNAVAILABLE")
    if decision.threshold is None:
        raise PaperIntakeRefusal("ADMISSION_THRESHOLD_ABSENT")
    if set(candidates) - set(decision.scores) or set(candidates) - set(
        decision.admitted
    ):
        raise PaperIntakeRefusal("RELEVANCE_SCORES_MISSING")
    admitted = [p for p in candidates if decision.admitted[p]]
    exclusions = tuple(
        Exclusion(paper_id=p, reason="below_relevance_threshold")
        for p in candidates
        if not decision.admitted[p]
    )
    ingestion = (
        await services.ingest(job.run_id, admitted)
        if admitted
        else f"no-admitted:{job.run_id}"
    )
    receipt = PaperIntakeReceipt(
        run_id=job.run_id,
        cycle_boundary_ms=job.cycle_boundary_ms,
        proposed_digest=_proposed(candidates),
        goal_set_digest=digest,
        ingested_papers=tuple(admitted),
        exclusions=exclusions,
        admission_receipt=decision.record_id,
        ingestion_receipt=ingestion,
        accepted_count=len(admitted),
    )
    await ledger.record(receipt, digest)
    return receipt
