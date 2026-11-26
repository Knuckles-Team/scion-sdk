"""Prepare-then-sign for a wallet operation (SCION-GOVERNED-WRITEBACK-R001.3).

The proof signs the durable change set, and the change set must not contain the
proof. ``prepare_wallet_operation`` builds the change set from the intent and the
confirmation alone and returns the values the signer binds: id, digest and
idempotency key. Execution rebuilds the identical change set from the same inputs
and the preparation, and refuses a rebuild that differs.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from scion_sdk.writeback.wallet_intent import (
    WalletExecutionEnvelope,
    wallet_plan_digest,
)
from scion_sdk.writeback_sinks.governed_grant import SinkGrant, build_change_set

__all__ = [
    "PREPARATION_KEY",
    "WalletPreparation",
    "WalletPreparationMismatch",
    "prepare_wallet_operation",
    "rebuild_prepared_change_set",
]

PREPARATION_KEY = "_wallet_preparation"


class WalletPreparation(BaseModel):
    """What a signer binds a proof to; a fact about one change set, never approval."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    change_set_id: str = Field(min_length=1)
    change_set_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1)
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    issued_ms: int = Field(ge=0)


class WalletPreparationMismatch(ValueError):
    """The change set rebuilt for execution differs from the prepared one."""

    code = "preparation_mismatch"


def prepare_wallet_operation(
    grant: SinkGrant, ops: dict[str, Any], *, now_ms: int
) -> WalletPreparation:
    """Build the change set from ``ops`` (the envelope without a proof) once."""
    envelope = _envelope(ops)
    change_set = build_change_set(grant, ops, now_ms=now_ms)
    return WalletPreparation(
        change_set_id=change_set.change_set_id,
        change_set_digest=change_set.change_set_digest,
        idempotency_key=change_set.idempotency_key,
        plan_digest=wallet_plan_digest(envelope.intent),
        issued_ms=now_ms,
    )


def rebuild_prepared_change_set(
    grant: SinkGrant, ops: dict[str, Any], preparation: WalletPreparation
) -> Any:
    """The exact prepared change set, or ``WalletPreparationMismatch``."""
    envelope = _envelope(ops)
    change_set = build_change_set(
        grant,
        ops,
        now_ms=preparation.issued_ms,
        change_set_id=preparation.change_set_id,
    )
    if (
        change_set.change_set_digest != preparation.change_set_digest
        or change_set.idempotency_key != preparation.idempotency_key
        or wallet_plan_digest(envelope.intent) != preparation.plan_digest
    ):
        raise WalletPreparationMismatch(
            "the wallet operation changed after preparation"
        )
    return change_set


def _envelope(ops: dict[str, Any]) -> WalletExecutionEnvelope:
    raw = ops.get("wallet_envelope")
    if raw is None:
        raise WalletPreparationMismatch("a wallet operation needs an envelope")
    return (
        raw
        if isinstance(raw, WalletExecutionEnvelope)
        else WalletExecutionEnvelope.model_validate(raw)
    )
