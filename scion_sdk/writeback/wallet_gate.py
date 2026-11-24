"""The envelope proof gate: pairs the execution envelope with the change set."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from scion_sdk.writeback.wallet_intent import (
    WalletExecutionEnvelope,
    wallet_plan_digest,
)
from scion_sdk.writeback.wallet_proof import (
    WalletKeyResolver,
    WalletProofBinding,
    WalletProofResult,
    WalletSignatureVerifier,
)

__all__ = ["EnvelopeProofGate"]


class EnvelopeProofGate:
    """Verify the envelope's proof against the exact durable change set.

    The binding comes from the authenticated tenant and actor, the
    durable change set and the business intent. The plan digest is always
    derived from the intent, never read from the confirmation or the
    proof: a confirmation of a different plan is a scope mismatch, and a
    missing proof is a missing signature. The proof nonce must equal the
    change-set id and the proof lifetime is at most 300 s, both enforced
    by the verifier. Everything else is the verifier's own refusal.
    """

    def __init__(
        self,
        envelope: WalletExecutionEnvelope,
        *,
        tenant_id: str,
        actor_id: str,
        resolver: WalletKeyResolver,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._envelope = envelope
        self._tenant_id = tenant_id
        self._actor_id = actor_id
        self._resolver = resolver
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    async def verify(self, change_set: Any) -> WalletProofResult:
        """Return the verification fact for this exact change set."""
        plan_digest = wallet_plan_digest(self._envelope.intent)
        if self._envelope.confirmation.plan_digest != plan_digest:
            return WalletProofResult(outcome="refused", reason="scope_mismatch")
        proof = self._envelope.proof
        if proof is None:
            return WalletProofResult(outcome="refused", reason="missing_signature")
        binding = WalletProofBinding(
            tenant_id=self._tenant_id,
            actor_id=self._actor_id,
            connector_id=change_set.connector_id,
            operation_id=self._envelope.intent.operation_id,
            account_id=self._envelope.intent.account_id,
            wallet_id=self._envelope.intent.wallet_id,
            network_id=self._envelope.intent.network_id,
            key_id=self._envelope.intent.key_id,
            plan_digest=plan_digest,
            change_set_digest=change_set.change_set_digest,
            change_set_id=change_set.change_set_id,
            idempotency_key=change_set.idempotency_key,
        )
        verifier = WalletSignatureVerifier(self._resolver)
        return await verifier.verify(proof, binding, now=self._clock_ms() // 1000)
