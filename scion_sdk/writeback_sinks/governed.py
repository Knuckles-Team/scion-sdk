"""Execute every write-back sink through the SDK governed write-back contract.

Per the AU boundary deconstruction spec, ``run_writeback`` no longer calls a sink directly. Each
invocation becomes one canonical :class:`SourceChangeSet` and runs through
:class:`~scion_sdk.writeback.connector.DurableWritableConnector`.
The SDK then validates the digest, expiry, field scope, base version and
authorization. It reserves an audit record before the source call and
appends the attempt to a :class:`WriteBackLedger`.

The change set carries the sink's JSON-safe operations as one ``ops`` field.
An AU sink applies a batch, not one entity field, and it has no source
read-back. The source version therefore comes from the caller's
``expected_source_version``/``current_source_version`` signals, or it is
``unversioned``. A sink failure during apply is recorded as an uncertain
outcome, and the original error is raised to the caller unchanged.

A dry run registers its change set in a throwaway ledger because it records
no effect. A live apply uses a durable file ledger under the AU runtime
directory, or an injected ledger root.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._env import run_sync_isolated, runtime_dir
from ._governed_common import PATCH_FIELD
from .core import WritebackContext, WritebackResult, WritebackSink
from .governed_audit import FileAuditReservation
from .governed_grant import (
    SinkGrant,
    build_change_set,
)
from .governed_transport import SinkWriteBackTransport

if TYPE_CHECKING:
    from epistemic_graph.generated.write_back import (
        SourceChangeSet,
        WriteBackAttempt,
    )

    from scion_sdk.writeback.connector import DurableWritableConnector

__all__ = [
    "PATCH_FIELD",
    "FileAuditReservation",
    "GovernedOutcome",
    "SinkGrant",
    "SinkWriteBackTransport",
    "build_change_set",
    "execute_governed",
    "ledger_root",
]


@dataclass(frozen=True)
class GovernedOutcome:
    """The sink result plus the SDK change-set evidence for one invocation."""

    result: WritebackResult
    change_set: SourceChangeSet
    attempt: WriteBackAttempt | None = None
    failure: BaseException | None = None

    def evidence(self) -> dict[str, Any]:
        """Return the JSON evidence a write-back manifest carries."""
        effect = self.attempt.effect_status.value if self.attempt else None
        return {
            "connector_id": self.change_set.connector_id,
            "change_set_id": self.change_set.change_set_id,
            "change_set_digest": self.change_set.change_set_digest,
            "authorization_mode": self.change_set.authorization.mode.value,
            "effect_status": effect,
        }


def ledger_root() -> Path:
    """Return the durable ledger root for live write-back change sets."""
    return runtime_dir() / "writeback-ledger"


def _connector(
    transport: SinkWriteBackTransport, change_set: SourceChangeSet, directory: Path
) -> DurableWritableConnector:
    from scion_sdk.writeback.connector import DurableWritableConnector
    from scion_sdk.writeback.durable_ledger import FileWriteBackLedger

    return DurableWritableConnector(
        change_set.connector_id,
        transport,
        FileWriteBackLedger(directory),
        audit=FileAuditReservation(directory),
    )


async def _dry_run(
    transport: SinkWriteBackTransport, change_set: SourceChangeSet, directory: Path
) -> None:
    connector = _connector(transport, change_set, directory)
    await connector.register(change_set)
    await connector.dry_run(change_set)


async def _apply(
    transport: SinkWriteBackTransport, change_set: SourceChangeSet, directory: Path
) -> WriteBackAttempt:
    connector = _connector(transport, change_set, directory)
    await connector.register(change_set)
    return await connector.apply(change_set)


def _preview(transport: SinkWriteBackTransport, change_set: SourceChangeSet) -> None:
    with tempfile.TemporaryDirectory(prefix="au-writeback-") as tmp:
        run_sync_isolated(
            lambda: asyncio.run(_dry_run(transport, change_set, Path(tmp)))
        )


def execute_governed(
    sink: WritebackSink,
    ctx: WritebackContext,
    ops: dict[str, Any],
    *,
    grant: SinkGrant,
    dry_run: bool,
    root: Path | None = None,
    _capture_failure: bool = False,
) -> GovernedOutcome:
    """Run one sink invocation through the SDK governed write-back contract.

    Raises the sink's own error after the SDK records an uncertain outcome.
    SDK refusals (expiry, scope, version, authorization, audit) raise
    :class:`~scion_sdk.writeback.errors.WriteBackError` before any
    live sink call.
    """
    change_set = build_change_set(grant, ops)
    transport = SinkWriteBackTransport(sink, ctx, ops)
    if dry_run:
        _preview(transport, change_set)
        return GovernedOutcome(_result(transport, grant), change_set)
    directory = (root or ledger_root()) / change_set.change_set_id
    attempt = run_sync_isolated(
        lambda: asyncio.run(_apply(transport, change_set, directory))
    )
    if transport.failure is not None:
        outcome = GovernedOutcome(
            _result(transport, grant), change_set, attempt, transport.failure
        )
        if _capture_failure:
            return outcome
        raise transport.failure
    return GovernedOutcome(_result(transport, grant), change_set, attempt)


def _result(transport: SinkWriteBackTransport, grant: SinkGrant) -> WritebackResult:
    if transport.result is None:
        return WritebackResult(target=grant.target)
    return transport.result
