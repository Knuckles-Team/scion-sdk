"""Route a claimed engine job to its handler by kind (SCION-SOURCE-INGEST-R005.27).

The engine owns the schedule: a firing enqueues a task, ``run_ingest_worker``
claims it, and ``dispatch_job`` runs the handler registered for its ``kind``. The
worker acks ``succeeded`` only after the handler returns, so a paper-intake job is
acked only after its receipt is recorded under the job's ``run_id``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from scion_sdk.runner.errors import PaperIntakeRefusal
from scion_sdk.runner.paper_intake import (
    IntakeLedger,
    PaperIntakeJob,
    PaperIntakeServices,
    run_paper_intake,
)

__all__ = [
    "PAPER_INTAKE_KIND",
    "JobHandler",
    "UnknownJobKind",
    "dispatch_job",
    "paper_intake_handler",
]

PAPER_INTAKE_KIND = "paper_intake"
JobHandler = Callable[[Mapping[str, Any]], Awaitable[object]]


class UnknownJobKind(Exception):
    """The claimed job names a kind with no registered handler; it is never guessed."""


async def _run(handler: JobHandler, job: Mapping[str, Any]) -> None:
    await handler(job)


def dispatch_job(
    handlers: Mapping[str, JobHandler],
) -> Callable[[dict[str, Any]], None]:
    """The ``run_job`` callable for ``run_ingest_worker``, routed by ``job['kind']``."""

    def run_job(job: dict[str, Any]) -> None:
        kind = job.get("kind")
        handler = handlers.get(kind) if isinstance(kind, str) else None
        if handler is None:
            raise UnknownJobKind(f"no handler for job kind {kind!r}")
        asyncio.run(_run(handler, job))

    return run_job


def paper_intake_handler(
    services: PaperIntakeServices,
    ledger: IntakeLedger,
    now_ms: Callable[[], int],
) -> JobHandler:
    """Handle a ``paper_intake`` job: run the cycle; deny scheduler access typed."""

    async def handle(job: Mapping[str, Any]) -> object:
        parsed = PaperIntakeJob.model_validate(job["payload"])
        try:
            return await run_paper_intake(
                parsed, services=services, ledger=ledger, now_ms=now_ms
            )
        except PermissionError:
            raise PaperIntakeRefusal("SCHEDULER_ACCESS_DENIED") from None

    return handle
