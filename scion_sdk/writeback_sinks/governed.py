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
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from scion_sdk.state_machine import (
    StateMachineDescriptor,
    next_state,
)
from scion_sdk.transition_refused import MachineTransitionRefused

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
    from medulla.generated.write_back import (
        SourceChangeSet,
        WriteBackAttempt,
    )

    from scion_sdk.writeback.connector import DurableWritableConnector
    from scion_sdk.writeback.wallet_gate import EnvelopeProofGate

__all__ = [
    "PATCH_FIELD",
    "FileAuditReservation",
    "GovernedOutcome",
    "SinkGrant",
    "SinkWriteBackTransport",
    "StaleRecordRefused",
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


class StaleRecordRefused(MachineTransitionRefused):
    """The proposal's read of the external record is stale.

    Carries ``record_version`` (the version the proposal read the state
    from) and ``expected_version`` (the change set's base version). Its
    code is ``STATE_STALE``.
    """

    code: ClassVar[str] = "STATE_STALE"

    def __init__(
        self,
        machine: str,
        state: str,
        event: str,
        record_version: str,
        expected_version: str,
    ) -> None:
        self.record_version = record_version
        self.expected_version = expected_version
        super().__init__(
            state,
            event,
            f"record version {record_version} does not match {expected_version}",
            machine=machine,
        )


def ledger_root() -> Path:
    """Return the durable ledger root for live write-back change sets."""
    return runtime_dir() / "writeback-ledger"


def _proof_gate(
    sink: WritebackSink, ctx: WritebackContext, ops: dict[str, Any]
) -> EnvelopeProofGate | None:
    """The proof gate of a sink that ``requires_wallet_proof``, else ``None``.

    The envelope is data in ``ops["wallet_envelope"]``, never a flag. A missing
    envelope refuses ``missing_signature`` before any transport call.
    """
    from scion_sdk.writeback.errors import WalletProofRefusedError
    from scion_sdk.writeback.wallet_gate import EnvelopeProofGate
    from scion_sdk.writeback.wallet_intent import WalletExecutionEnvelope
    from scion_sdk.writeback.wallet_trust import EngineWalletKeyResolver

    if not getattr(sink, "requires_wallet_proof", False):
        return None
    envelope = ops.get("wallet_envelope")
    if envelope is None:
        raise WalletProofRefusedError("missing_signature")
    if not isinstance(envelope, WalletExecutionEnvelope):
        envelope = WalletExecutionEnvelope.model_validate(envelope)
    resolver = ctx.wallet_resolver or EngineWalletKeyResolver(ctx.engine)
    return EnvelopeProofGate(
        envelope, tenant_id=ctx.tenant_id, actor_id=ctx.actor_id, resolver=resolver
    )


def _connector(
    transport: SinkWriteBackTransport,
    change_set: SourceChangeSet,
    directory: Path,
    proof_gate: EnvelopeProofGate | None = None,
) -> DurableWritableConnector:
    from scion_sdk.writeback.connector import DurableWritableConnector
    from scion_sdk.writeback.durable_ledger import FileWriteBackLedger

    return DurableWritableConnector(
        change_set.connector_id,
        transport,
        FileWriteBackLedger(directory),
        audit=FileAuditReservation(directory),
        proof_gate=proof_gate,
    )


async def _dry_run(
    transport: SinkWriteBackTransport, change_set: SourceChangeSet, directory: Path
) -> None:
    connector = _connector(transport, change_set, directory)
    await connector.register(change_set)
    await connector.dry_run(change_set)


async def _apply(
    transport: SinkWriteBackTransport,
    change_set: SourceChangeSet,
    directory: Path,
    proof_gate: EnvelopeProofGate | None = None,
) -> WriteBackAttempt:
    connector = _connector(transport, change_set, directory, proof_gate)
    await connector.register(change_set)
    return await connector.apply(change_set)


def _preview(transport: SinkWriteBackTransport, change_set: SourceChangeSet) -> None:
    with tempfile.TemporaryDirectory(prefix="au-writeback-") as tmp:
        run_sync_isolated(
            lambda: asyncio.run(_dry_run(transport, change_set, Path(tmp)))
        )


def _require_state_machine(ops: dict[str, Any], change_set: SourceChangeSet) -> None:
    """Refuse a stale or illegal record transition before any sink call.

    A proposal without a descriptor passes unchanged. With one,
    ``ops["_state_machine"]`` carries the declared
    :class:`~scion_sdk.state_machine.StateMachineDescriptor`, the
    proposal's read of the external record (state, event and guard
    facts) and the record version that read was taken from. A record
    version that does not match the change set's base version is
    stale; an event the machine does not declare, or whose guard does
    not hold, is refused by ``next_state`` itself.
    """
    machine = ops.get("_state_machine")
    if machine is None:
        return
    descriptor = machine.get("descriptor")
    state = machine.get("state")
    event = machine.get("event")
    record_version = machine.get("record_version")
    facts = machine.get("facts") or {}
    if (
        not isinstance(descriptor, StateMachineDescriptor)
        or not isinstance(state, str)
        or not isinstance(event, str)
        or not isinstance(record_version, str)
        or not record_version
        or not isinstance(facts, Mapping)
    ):
        raise ValueError("state-machine payload is incomplete")
    if record_version != change_set.base_source_version:
        raise StaleRecordRefused(
            descriptor.name,
            state,
            event,
            record_version,
            change_set.base_source_version,
        )
    next_state(descriptor, state, event, facts)


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

    A proposal carrying a state-machine descriptor is checked against the
    machine first: a stale record version refuses with ``STATE_STALE`` and
    an illegal transition with ``STATE_TRANSITION_REFUSED``. A proposal
    without a descriptor passes unchanged.

    Raises the sink's own error after the SDK records an uncertain outcome.
    SDK refusals (expiry, scope, version, authorization, audit, stale or
    illegal record transition) raise before any live sink call.
    """
    change_set = build_change_set(grant, ops)
    _require_state_machine(ops, change_set)
    gate = None if dry_run else _proof_gate(sink, ctx, ops)
    transport = SinkWriteBackTransport(sink, ctx, ops)
    if dry_run:
        _preview(transport, change_set)
        return GovernedOutcome(_result(transport, grant), change_set)
    directory = (root or ledger_root()) / change_set.change_set_id
    attempt = run_sync_isolated(
        lambda: asyncio.run(_apply(transport, change_set, directory, gate))
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
