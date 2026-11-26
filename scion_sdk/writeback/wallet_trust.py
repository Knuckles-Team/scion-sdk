"""Wallet key trust resolved from the engine under the caller's tenant.

SCION-GOVERNED-WRITEBACK-R001.3. The engine serves the trust record
(``WalletKeyTrustGet``); while the pinned engine client lacks that method the
resolver refuses with ``EngineCapabilityUnavailable`` instead of answering.
"""

from __future__ import annotations

import importlib
from datetime import datetime
from typing import Any

from scion_sdk.engine_capability import require_engine_capability
from scion_sdk.writeback.wallet_proof import WalletKeyTrust, WalletProofBinding

__all__ = ["EngineWalletKeyResolver"]


def _seconds(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def _public_key(value: str) -> bytes:
    """The engine serves ``public_key`` as lower-case hex of exactly 32 bytes."""
    try:
        key = bytes.fromhex(value)
    except ValueError:
        raise ValueError("wallet trust public_key is not hex") from None
    if len(key) != 32 or value != value.lower():
        raise ValueError("wallet trust public_key is not 32 lower-case hex bytes")
    return key


class EngineWalletKeyResolver:
    """Resolve a wallet key binding from the engine's trust record."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def resolve(self, binding: WalletProofBinding) -> WalletKeyTrust | None:
        """The current trust for ``binding``, or ``None`` for any foreign or absent record."""
        require_engine_capability("WalletKeyTrustGet")
        compute = importlib.import_module("medulla.generated.compute")
        try:
            record = await compute.send_wallet_key_trust_get(
                self._client,
                {
                    "account_id": binding.account_id,
                    "network_id": binding.network_id,
                    "wallet_id": binding.wallet_id,
                },
            )
        except Exception as error:
            # The engine refuses an absent record with this stable code; an absent
            # record answers exactly like a foreign one.
            if getattr(error, "code", None) == "WALLET_KEY_TRUST_NOT_FOUND":
                return None
            raise
        if (
            record is None
            or record.tenant_id != binding.tenant_id
            or record.key_id != binding.key_id
        ):
            return None
        return WalletKeyTrust(
            binding=binding,
            public_key=_public_key(record.public_key),
            signer_ref=record.signer_ref,
            trust_revision=str(record.trust_revision),
            valid_from=_seconds(record.valid_from),
            valid_until=(
                _seconds(record.valid_until) if record.valid_until else 2**63 - 1
            ),
            revoked=bool(record.revoked),
        )
