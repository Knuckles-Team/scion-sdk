"""Wallet proof, prepare-then-sign and governed apply, end to end through the sink path.

SCION-GOVERNED-WRITEBACK-R001.6. The engine trust record is served by a structural
double of ``WalletKeyTrustGet`` (the pinned client serves the method; seeding a trust
record into a live engine needs a harness the repository does not have, see the
overstory gap list). Everything else is the production path: ``prepare``, confirm,
sign, then ``execute_governed`` through the durable ledger.
"""

from __future__ import annotations

import base64
import importlib
import json
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scion_sdk.writeback.errors import (
    WalletProofRefusedError,
    WriteBackPersistenceError,
)
from scion_sdk.writeback.wallet_intent import (
    WalletBusinessIntent,
    WalletConfirmation,
    WalletExecutionEnvelope,
    wallet_plan_digest,
)
from scion_sdk.writeback.wallet_proof import WalletProofBinding, WalletProofRequest
from scion_sdk.writeback_sinks import core
from scion_sdk.writeback_sinks.governed import (
    PROOF_EVIDENCE_FILE,
    SinkGrant,
    execute_governed,
)
from scion_sdk.writeback_sinks.wallet_prepare import (
    PREPARATION_KEY,
    WalletPreparation,
    WalletPreparationMismatch,
    prepare_wallet_operation,
    rebuild_prepared_change_set,
)

_BOUND = (
    "tenant_id",
    "actor_id",
    "connector_id",
    "operation_id",
    "account_id",
    "wallet_id",
    "network_id",
    "key_id",
    "plan_digest",
    "change_set_digest",
    "idempotency_key",
)


class _Sink:
    domain = "walletfake"
    enable_flag = "WALLETFAKE_ENABLE_WRITE"
    requires_wallet_proof = True

    def __init__(self) -> None:
        self.effects = 0

    def run(self, ctx: Any, ops: Any, *, dry_run: bool) -> Any:
        if not dry_run:
            self.effects += 1
        return core.WritebackResult(target=self.domain)


@dataclass
class _World:
    key: Ed25519PrivateKey
    trust: dict[str, Any]
    now_ms: int


def _intent(**changes: Any) -> WalletBusinessIntent:
    base: dict[str, Any] = {
        "operation_id": "pay",
        "account_id": "account",
        "wallet_id": "wallet",
        "network_id": "network",
        "key_id": "key",
        "params": {"amount": 100},
    }
    return WalletBusinessIntent(**{**base, **changes})


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    trust: dict[str, Any] = {
        "tenant_id": "tenant",
        "key_id": "key",
        "public_key": public.hex(),
        "signer_ref": "signer",
        "trust_revision": 7,
        "valid_from": "1970-01-01T00:00:00Z",
        "valid_until": None,
        "revoked": None,
    }
    compute = importlib.import_module("medulla.generated.compute")

    async def served(client: Any, params: dict[str, str]) -> Any:
        assert set(params) == {"account_id", "network_id", "wallet_id"}
        return SimpleNamespace(**trust) if trust else None

    monkeypatch.setattr(compute, "send_wallet_key_trust_get", served, raising=False)
    return _World(key, trust, time.time_ns() // 1_000_000)


def _grant() -> SinkGrant:
    return SinkGrant(
        target=_Sink.domain, enable_flag=_Sink.enable_flag, write_enabled=True
    )


def _prepared(
    world: _World,
    *,
    intent: WalletBusinessIntent | None = None,
    confirm_plan: str | None = None,
    tenant: str = "tenant",
    issued_offset_s: int = 0,
    sign: bool = True,
) -> dict[str, Any]:
    intent = intent or _intent()
    plan = wallet_plan_digest(intent)
    confirmation = WalletConfirmation(
        confirmed_by="actor",
        confirmed_at_ms=world.now_ms,
        plan_digest=confirm_plan or plan,
    )
    bare = WalletExecutionEnvelope(intent=intent, confirmation=confirmation, proof=None)
    ops: dict[str, Any] = {"creations": [{"name": "a"}], "wallet_envelope": bare}
    preparation = prepare_wallet_operation(_grant(), ops, now_ms=world.now_ms)
    change_set = rebuild_prepared_change_set(_grant(), ops, preparation)
    proof = None
    if sign:
        binding = WalletProofBinding(
            tenant_id=tenant,
            actor_id="actor",
            connector_id=change_set.connector_id,
            operation_id=intent.operation_id,
            account_id=intent.account_id,
            wallet_id=intent.wallet_id,
            network_id=intent.network_id,
            key_id=intent.key_id,
            plan_digest=plan,
            change_set_digest=change_set.change_set_digest,
            change_set_id=change_set.change_set_id,
            idempotency_key=change_set.idempotency_key,
        )
        issued = world.now_ms // 1000 + issued_offset_s
        unsigned = WalletProofRequest(
            **{name: getattr(binding, name) for name in _BOUND},
            scheme="ed25519_jcs",
            nonce=binding.change_set_id,
            issued_at=issued,
            expires_at=issued + 200,
            signature="A" * 86,
        )
        raw = world.key.sign(unsigned.signed_bytes())
        signature = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        proof = unsigned.model_copy(update={"signature": signature})
    return {
        **ops,
        "wallet_envelope": bare.model_copy(update={"proof": proof}),
        PREPARATION_KEY: preparation,
    }


def _run(sink: _Sink, ops: dict[str, Any], root: Any, tenant: str = "tenant") -> Any:
    ctx = core.WritebackContext(tenant_id=tenant, actor_id="actor", engine=object())
    return execute_governed(sink, ctx, ops, grant=_grant(), dry_run=False, root=root)


@pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R001.6")
def test_served_wallet_proof_and_governed_apply(world: _World, tmp_path: Any) -> None:
    sink = _Sink()
    ops = _prepared(world)
    assert isinstance(ops[PREPARATION_KEY], WalletPreparation)
    outcome = _run(sink, ops, tmp_path)
    assert sink.effects == 1 and outcome.attempt is not None
    evidence_file = tmp_path / ops[PREPARATION_KEY].change_set_id / PROOF_EVIDENCE_FILE
    stored = json.loads(evidence_file.read_text())
    assert stored["trust_revision"] == "7" and stored["signer_ref"] == "signer"

    # After a restart the durable receipt stands: a sink has no read-back, so the same
    # request is refused as a persistence error and never mutates a second time.
    with pytest.raises(WriteBackPersistenceError):
        _run(sink, ops, tmp_path)
    assert sink.effects == 1

    refusals: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
        ("scope_mismatch", _prepared(world, confirm_plan="b" * 64), {}),
        ("missing_signature", _prepared(world, sign=False), {}),
        ("scope_mismatch", _prepared(world), {"tenant": "other"}),
        ("untrusted_key", _prepared(world, intent=_intent(key_id="other-key")), {}),
        ("expired", _prepared(world, issued_offset_s=-1000), {}),
    ]
    for reason, refused_ops, extra in refusals:
        fresh = _Sink()
        with pytest.raises(WalletProofRefusedError) as error:
            _run(fresh, refused_ops, tmp_path, **extra)
        assert error.value.reason == reason and fresh.effects == 0

    world.trust["revoked"] = True
    revoked = _Sink()
    with pytest.raises(WalletProofRefusedError) as error:
        _run(revoked, _prepared(world), tmp_path)
    assert error.value.reason == "revoked_key" and revoked.effects == 0


@pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R001.6")
def test_intent_tampering_after_preparation_makes_no_effect(
    world: _World, tmp_path: Any
) -> None:
    sink = _Sink()
    ops = _prepared(world)
    envelope = ops["wallet_envelope"]
    tampered = envelope.model_copy(
        update={"intent": _intent(params={"amount": 9_999_999})}
    )
    with pytest.raises(WalletPreparationMismatch):
        _run(sink, {**ops, "wallet_envelope": tampered}, tmp_path)
    assert sink.effects == 0
