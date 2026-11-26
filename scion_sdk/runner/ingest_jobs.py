"""Ingest jobs submitted through the engine queue after a content sync.

SCION-SOURCE-INGEST-R907. Document and codebase jobs go through the client's
``ingest_enqueue`` and full-source syncs through ``start_source_drain``.
``INGEST_QUEUE_FULL`` is a signal to slow down: the runner pauses with a doubling
delay and defers what it could not submit; it never drops a job silently.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = [
    "INGEST_QUEUE_FULL",
    "IngestJob",
    "IngestQueueUnavailable",
    "submit_jobs",
]

INGEST_QUEUE_FULL = "INGEST_QUEUE_FULL"
_METHODS = {
    "document": "ingest_enqueue",
    "codebase": "ingest_enqueue",
    "source_drain": "start_source_drain",
}


class IngestQueueUnavailable(RuntimeError):
    """The engine client does not serve the queue method; nothing was submitted."""

    def __init__(self, method: str) -> None:
        self.method = method
        super().__init__(f"the engine client does not serve {method}")


@dataclass(frozen=True)
class IngestJob:
    kind: Literal["document", "codebase", "source_drain"]
    source: str
    ref: str = ""


@dataclass
class JobReport:
    submitted: list[IngestJob] = field(default_factory=list)
    deferred: list[IngestJob] = field(default_factory=list)
    pauses: list[float] = field(default_factory=list)


async def submit_jobs(
    client: Any,
    jobs: tuple[IngestJob, ...],
    *,
    sleep: Callable[[float], Awaitable[None]],
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    max_pauses: int = 5,
) -> JobReport:
    """Submit ``jobs`` in order; pause and retry on ``INGEST_QUEUE_FULL``."""
    report = JobReport()
    delay = initial_delay
    pending = list(jobs)
    while pending:
        job = pending[0]
        method = _METHODS[job.kind]
        call = getattr(client, method, None)
        if call is None:
            raise IngestQueueUnavailable(method)
        try:
            await (
                call(job.source)
                if job.kind == "source_drain"
                else call(job.source, job.ref)
            )
        except Exception as error:
            if getattr(error, "code", None) != INGEST_QUEUE_FULL:
                raise
            if len(report.pauses) >= max_pauses:
                report.deferred.extend(pending)
                return report
            report.pauses.append(delay)
            await sleep(delay)
            delay = min(delay * 2, max_delay)
            continue
        report.submitted.append(pending.pop(0))
    return report
