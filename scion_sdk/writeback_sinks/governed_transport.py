"""Adapt an AU write-back sink call to the SDK write-back transport port."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

from scion_sdk.writeback.errors import OutcomeUncertainError
from scion_sdk.writeback.models import DryRunObservation, SourceSnapshot

from ._governed_common import PATCH_FIELD, _digest, _write_back
from .core import WritebackContext, WritebackResult, WritebackSink

if TYPE_CHECKING:
    from epistemic_graph.generated.write_back import (
        ReconciliationObservation,
        SourceChangeSet,
        WriteBackAttempt,
    )

__all__ = ["SinkWriteBackTransport"]


class SinkWriteBackTransport:
    """Adapt one AU :class:`WritebackSink` call to the SDK transport port.

    The sink runs on a worker thread, so a sink that bridges to async code
    never re-enters the SDK's event loop.
    """

    def __init__(
        self, sink: WritebackSink, ctx: WritebackContext, ops: dict[str, Any]
    ) -> None:
        self._sink = sink
        self._ctx = ctx
        self._ops = ops
        self._effect: WriteBackAttempt | None = None
        self.result: WritebackResult | None = None
        self.failure: BaseException | None = None

    async def read_current(self, change_set: SourceChangeSet) -> SourceSnapshot:
        """Report the caller's current version signal; sinks have no read-back."""
        version = self._ops.get("current_source_version")
        return SourceSnapshot(
            source_version=str(version or change_set.base_source_version),
            fields={PATCH_FIELD: None},
        )

    async def preview(
        self, change_set: SourceChangeSet, current: SourceSnapshot
    ) -> DryRunObservation:
        """Run the sink in dry-run mode and bind its proposals to the change set."""
        self.result = await self._run(dry_run=True)
        return DryRunObservation(
            change_set_digest=change_set.change_set_digest,
            source_version=current.source_version,
            desired_patch_digest=change_set.patch_digest(),
            changed_fields=(PATCH_FIELD,),
            before={PATCH_FIELD: None},
            after=dict(change_set.desired_patch),
        )

    async def prior_effect(
        self, change_set: SourceChangeSet
    ) -> WriteBackAttempt | None:
        """Return this invocation's effect; each invocation is a new change set."""
        return self._effect

    async def apply(
        self, change_set: SourceChangeSet, expected_version: str
    ) -> WriteBackAttempt:
        """Run the sink live; any sink error is an uncertain outcome."""
        try:
            self.result = await self._run(dry_run=False)
        except asyncio.CancelledError as exc:
            self.failure = exc
            raise OutcomeUncertainError("sink call cancelled") from exc
        except Exception as exc:  # a partial batch may have applied
            self.failure = exc
            raise OutcomeUncertainError(str(exc)) from exc
        self._effect = self._attempt(change_set, expected_version)
        return self._effect

    async def reconcile(self, change_set: SourceChangeSet) -> ReconciliationObservation:
        """Sinks cannot observe the source, so the outcome stays uncertain."""
        return cast(
            "ReconciliationObservation",
            _write_back().ReconciliationObservation(
                tenant_id=change_set.tenant_id,
                change_set_id=change_set.change_set_id,
                change_set_digest=change_set.change_set_digest,
                idempotency_key=change_set.idempotency_key,
                observed_source_version=change_set.base_source_version,
                effect_status=_write_back().WriteBackEffectStatus.OUTCOME_UNCERTAIN,
                retry_allowed=False,
                evidence_digest=_digest({"reason": "sink has no read-back"}),
                connector_observation_digest=_digest({"target": self._sink.domain}),
                provenance_digest=_digest(change_set.field_provenance),
            ),
        )

    async def _run(self, *, dry_run: bool) -> WritebackResult:
        return await asyncio.to_thread(
            self._sink.run, self._ctx, self._ops, dry_run=dry_run
        )

    def _attempt(
        self, change_set: SourceChangeSet, expected_version: str
    ) -> WriteBackAttempt:
        authorization = change_set.authorization
        counts = self.result.as_dict() if self.result is not None else {}
        return cast(
            "WriteBackAttempt",
            _write_back().WriteBackAttempt(
                tenant_id=change_set.tenant_id,
                change_set_id=change_set.change_set_id,
                change_set_digest=change_set.change_set_digest,
                idempotency_key=change_set.idempotency_key,
                kind=_write_back().WriteBackAttemptKind.APPLY,
                input_digest=authorization.input_digest,
                output_digest=authorization.output_digest,
                pre_source_version=expected_version,
                post_source_version=expected_version,
                applied_field_digest=change_set.patch_digest(),
                outcome=_write_back().WriteBackOutcome.APPLIED,
                effect_status=_write_back().WriteBackEffectStatus.APPLIED,
                connector_observation_digest=_digest(counts),
                provenance_digest=_digest(change_set.field_provenance),
            ),
        )
