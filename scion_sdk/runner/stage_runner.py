"""Concurrent, dependency-ordered, resumable stage execution.

SCION-SOURCE-INGEST-R001.2. Each stage is one ``sync_stream`` over its own
stream, so the durable ``source_status`` read and ``commit_page`` apply
unchanged. A stage starts when every prerequisite has completed with the
receipt the dependency names. A failed or blocked stage never gains another
stage's receipt, and a sibling's success keeps its own.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Literal

import anyio
from medulla.generated.source_ingestion import SourceCheckpoint

from scion_sdk.ports.session import McpSession
from scion_sdk.ports.source_adapter import SourceAdapter
from scion_sdk.runner.errors import StageRefusal
from scion_sdk.runner.logs import structured
from scion_sdk.runner.stages import StageDescriptor, validate_stage_graph
from scion_sdk.runner.syncing import SyncTarget, sync_stream

__all__ = ["StageReport", "StageResult", "receipt_id", "run_stages"]

_logger = logging.getLogger(__name__)
Outcome = Literal["complete", "failed", "blocked", "cancelled"]
_GRAPH_CODES = (
    ("cyclic", "STAGE_CYCLE"),
    ("duplicate", "STAGE_IDENTITY_COLLISION"),
)


def receipt_id(checkpoint: SourceCheckpoint | None) -> str:
    """The identity of an accepted checkpoint; empty when nothing is accepted."""
    if checkpoint is None:
        return ""
    body = json.dumps(checkpoint.model_dump(mode="json"), sort_keys=True)
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


@dataclass(frozen=True)
class StageResult:
    """What one stage durably did."""

    stage_id: str
    outcome: Outcome
    previous_checkpoint: SourceCheckpoint | None
    offered_checkpoint: SourceCheckpoint | None
    accepted_receipt: str
    dependency_receipts: tuple[str, ...]
    reason: str = ""


@dataclass(frozen=True)
class StageReport:
    """The evidence of one pass: ids, receipts and outcomes, never payloads."""

    run_id: str
    results: tuple[StageResult, ...]

    @property
    def succeeded(self) -> bool:
        return all(result.outcome == "complete" for result in self.results)

    def as_document(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "succeeded": self.succeeded,
            "stages": [
                {
                    "stage_id": r.stage_id,
                    "outcome": r.outcome,
                    "dependency_receipts": list(r.dependency_receipts),
                    "previous_checkpoint": r.previous_checkpoint
                    and r.previous_checkpoint.model_dump(mode="json"),
                    "offered_checkpoint": r.offered_checkpoint
                    and r.offered_checkpoint.model_dump(mode="json"),
                    "accepted_receipt": r.accepted_receipt,
                    "reason": r.reason,
                }
                for r in self.results
            ],
        }


def refuse_unsafe_graph(target: SyncTarget) -> None:
    """Check the stage graph before the first source call."""
    if not target.stages:
        raise StageRefusal("STAGE_LIMITS_ABSENT")
    try:
        validate_stage_graph(target.stages, target.connector, target.tenant)
    except ValueError as error:
        message = str(error)
        code = next((c for word, c in _GRAPH_CODES if word in message), None)
        raise StageRefusal(code or "STAGE_SCOPE_MISMATCH") from None


class _Pass:
    def __init__(
        self,
        target: SyncTarget,
        session: McpSession,
        adapters: Mapping[str, SourceAdapter],
    ) -> None:
        self.target = replace(target, stages=())
        self.session = session
        self.adapters = adapters
        self.results: dict[str, StageResult] = {}
        self.events = {s.stage_id: anyio.Event() for s in target.stages}

    async def _durable(self, stream: str) -> SourceCheckpoint | None:
        status = await self.target.sink.source_status(self.target.connector, stream)
        return status.accepted_checkpoint

    def _blocked(self, stage: StageDescriptor) -> StageResult | None:
        for dep in stage.dependencies:
            prerequisite = self.results[dep.stage_id]
            if prerequisite.outcome != "complete":
                return self._result(stage, "blocked", reason="STAGE_DEPENDENCY_FAILED")
            if prerequisite.accepted_receipt != dep.receipt_id:
                return self._result(stage, "blocked", reason="STAGE_RECEIPT_MISMATCH")
        return None

    def _result(
        self,
        stage: StageDescriptor,
        outcome: Outcome,
        *,
        previous: SourceCheckpoint | None = None,
        offered: SourceCheckpoint | None = None,
        reason: str = "",
    ) -> StageResult:
        receipts = tuple(
            self.results[d.stage_id].accepted_receipt
            for d in stage.dependencies
            if d.stage_id in self.results
        )
        accepted = receipt_id(offered) if outcome == "complete" else ""
        return StageResult(
            stage.stage_id, outcome, previous, offered, accepted, receipts, reason
        )

    async def _attempt(
        self, stage: StageDescriptor, *, retry: bool = True
    ) -> StageResult:
        adapter = self.adapters.get(stage.source)
        if adapter is None:
            return self._result(stage, "failed", reason="STAGE_SCOPE_MISMATCH")
        previous = await self._durable(stage.stream)
        deadline = stage.admitted_limits.deadline_ms / 1000
        try:
            with anyio.fail_after(deadline):
                outcome = await sync_stream(self.session, adapter, self.target)
        except TimeoutError:
            return await self._after_timeout(stage, previous, retry=retry)
        except anyio.get_cancelled_exc_class():
            with anyio.CancelScope(shield=True):
                offered = await self._durable(stage.stream)
                self.results[stage.stage_id] = self._result(
                    stage, "cancelled", previous=previous, offered=offered
                )
            raise
        except Exception as error:
            return self._result(
                stage, "failed", previous=previous, reason=type(error).__name__
            )
        offered = outcome.checkpoint
        if offered is not None and offered.stream != stage.stream:
            return self._result(stage, "failed", reason="STAGE_PROGRESS_INHERITED")
        return self._result(stage, "complete", previous=previous, offered=offered)

    async def _after_timeout(
        self, stage: StageDescriptor, previous: SourceCheckpoint | None, *, retry: bool
    ) -> StageResult:
        """Read the durable state back; retry only when nothing advanced."""
        current = await self._durable(stage.stream)
        if current != previous:
            return self._result(
                stage,
                "failed",
                previous=previous,
                offered=current,
                reason="TIMEOUT_AFTER_COMMIT",
            )
        if retry:
            return await self._attempt(stage, retry=False)
        return self._result(
            stage, "failed", previous=previous, offered=current, reason="TIMEOUT"
        )

    async def drive(
        self, stage: StageDescriptor, limiter: anyio.CapacityLimiter
    ) -> None:
        try:
            for dep in stage.dependencies:
                await self.events[dep.stage_id].wait()
            result = self._blocked(stage)
            if result is None:
                async with limiter:
                    result = await self._attempt(stage)
            self.results[stage.stage_id] = result
        finally:
            self.events[stage.stage_id].set()


async def run_stages(
    target: SyncTarget,
    session: McpSession,
    adapters: Mapping[str, SourceAdapter],
    *,
    run_id: str,
) -> StageReport:
    """Run every stage of ``target`` and report what each durably did."""
    refuse_unsafe_graph(target)
    limit = min(s.admitted_limits.max_active_stages for s in target.stages)
    limiter = anyio.CapacityLimiter(limit)
    run = _Pass(target, session, adapters)
    async with anyio.create_task_group() as group:
        for stage in target.stages:
            group.start_soon(run.drive, stage, limiter)
    report = StageReport(run_id, tuple(run.results[s.stage_id] for s in target.stages))
    _logger.info(
        "stages %s finished",
        run_id,
        extra=structured(
            "stages_finished",
            target.connector,
            run_id=run_id,
            outcomes={r.stage_id: r.outcome for r in report.results},
        ),
    )
    return report
