"""Resolve wallet key trust from the engine trust record, under the caller's tenant.

SCION-GOVERNED-WRITEBACK-R001.3. The engine record is the only authority: a
record of another tenant, account, wallet, network or key answers exactly like an
absent one (no existence oracle).
"""

from __future__ import annotations

import datetime
from typing import Any

from scion_sdk.writeback.wallet_proof import WalletKeyTrust, WalletProofBinding

__all__ = ["EngineWalletKeyResolver"]


def _epoch_seconds(value: str) -> int:
    return int(datetime.datetime.fromisoformat(value).timestamp())


class EngineWalletKeyResolver:
    """``WalletKeyResolver`` over the engine ``WalletKeyTrustGet`` method."""

    def __init__(self, client: Any, *, graph: str | None = None) -> None:
        self._client = client
        self._graph = graph

    async def resolve(self, binding: WalletProofBinding) -> WalletKeyTrust | None:
        """The current trust of the binding's key, or ``None`` when none applies."""
        from medulla.generated.compute import (
            decode_wallet_key_trust_get,
            send_wallet_key_trust_get,
        )

        raw = await send_wallet_key_trust_get(
            self._client,
            {
                "account_id": binding.account_id,
                "network_id": binding.network_id,
                "wallet_id": binding.wallet_id,
            },
            self._graph,
        )
        record = decode_wallet_key_trust_get(raw)
        if record is None:
            return None
        if (
            record.tenant_id != binding.tenant_id
            or record.key_id != binding.key_id
            or record.account_id != binding.account_id
            or record.wallet_id != binding.wallet_id
            or record.network_id != binding.network_id
        ):
            return None
        until = record.valid_until
        return WalletKeyTrust(
            binding=binding,
            public_key=bytes.fromhex(record.public_key),
            signer_ref=record.signer_ref,
            trust_revision=str(record.trust_revision),
            valid_from=_epoch_seconds(record.valid_from),
            valid_until=_epoch_seconds(until) if until else 2**53 - 1,
            revoked=bool(record.revoked),
        )
