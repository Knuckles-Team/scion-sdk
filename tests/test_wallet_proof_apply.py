"""Wallet proof is verified before a governed effect and never replaces approval."""

import base64
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from medulla.generated.write_back import (
    WriteBackAttempt,
    WriteBackAttemptKind,
    WriteBackEffectStatus,
    WriteBackOutcome,
)
from pydantic import ValidationError

from scion_sdk.testing.writeback import make_writeback_fixture
from scion_sdk.testing.writeback_audit import InMemoryAuditReservation
from scion_sdk.writeback import (
    EnvelopeProofGate,
    WalletBusinessIntent,
    WalletConfirmation,
    WalletExecutionEnvelope,
    WalletKeyTrust,
    WalletProofBinding,
    WalletProofRequest,
    WalletProofResult,
    WalletSignatureVerifier,
    wallet_plan_digest,
)
from scion_sdk.writeback.authorization import (
    DeterministicAuthorizationVerifier,
)
from scion_sdk.writeback.errors import (
    AuthorizationDeniedError,
    WalletProofRefusedError,
)
from scion_sdk.writeback.service import GovernedWriteBack

NOW = 1050


class _Gate:
    """Pairs a received proof with the authenticated binding of the change set."""

    def __init__(self, proof, trust_for, now=NOW):
        self.proof, self.trust_for, self.now = proof, trust_for, now
        self.calls = 0

    async def verify(self, change_set):
        self.calls += 1
        binding = _binding(change_set)

        class _Resolver:
            async def resolve(_, wanted):
                return self.trust_for(wanted)

        return await WalletSignatureVerifier(_Resolver()).verify(
            self.proof, binding, now=self.now
        )


def _binding(change_set, plan_digest="a" * 64):
    return WalletProofBinding(
        tenant_id=change_set.tenant_id,
        actor_id="actor",
        connector_id=change_set.connector_id,
        operation_id="pay",
        account_id="account",
        wallet_id="wallet",
        network_id="network",
        key_id="key",
        plan_digest=plan_digest,
        change_set_digest=change_set.change_set_digest,
        change_set_id=change_set.change_set_id,
        idempotency_key=change_set.idempotency_key,
    )


def _signed(change_set, plan_digest="a" * 64):
    binding = _binding(change_set, plan_digest)
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    unsigned = WalletProofRequest(
        **{
            name: getattr(binding, name)
            for name in (
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
        },
        scheme="ed25519_jcs",
        nonce=binding.change_set_id,
        issued_at=1000,
        expires_at=1100,
        signature="A" * 86,
    )
    signature = base64.urlsafe_b64encode(key.sign(unsigned.signed_bytes()))
    proof = unsigned.model_copy(update={"signature": signature.decode().rstrip("=")})
    trust = WalletKeyTrust(binding, public, "signer", "revision", 900, 1400, False)
    return proof, trust


def _TrustResolver(trust):
    class _Resolver:
        async def resolve(_, wanted):
            return trust

    return _Resolver()


def _port(fixture, gate, *, authorized=True, clock_ms=None, verifier=None):
    verifier = verifier or DeterministicAuthorizationVerifier()
    if authorized:
        verifier.grant(fixture.change_set)
    return GovernedWriteBack(
        fixture.transport,
        verifier,
        InMemoryAuditReservation(),
        proof_gate=gate,
        clock_ms=clock_ms,
    )


async def _status(fixture):
    return (await fixture.transport.read_current(fixture.change_set)).fields["status"]


def test_proof_models_are_closed_and_a_result_is_not_an_authorization_grant():
    fixture = make_writeback_fixture()
    proof, _ = _signed(fixture.change_set)
    with pytest.raises(ValidationError):
        WalletProofRequest.model_validate({**proof.model_dump(), "authorized": True})
    with pytest.raises(ValidationError):
        WalletProofResult.model_validate(
            {"outcome": "verified", "reason": "verified", "authorized": True}
        )
    assert "authorized" not in WalletProofResult.model_fields
    with pytest.raises(ValidationError):
        proof.tenant_id = "other"  # type: ignore[misc]


async def test_verified_proof_precedes_one_effect_but_does_not_replace_approval():
    fixture = make_writeback_fixture()
    proof, trust = _signed(fixture.change_set)
    gate = _Gate(proof, lambda _: trust)
    attempt = await _port(fixture, gate).apply(fixture.change_set)
    assert attempt.idempotency_key == fixture.change_set.idempotency_key
    assert await _status(fixture) == "approved"
    assert gate.calls >= 1

    unapproved = make_writeback_fixture()
    proof, trust = _signed(unapproved.change_set)
    with pytest.raises(AuthorizationDeniedError):
        await _port(unapproved, _Gate(proof, lambda _: trust), authorized=False).apply(
            unapproved.change_set
        )
    assert await _status(unapproved) == "new"


@pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R001.2")
async def test_gate_binds_envelope_and_replay_returns_prior_effect_without_fresh_proof():
    fixture = make_writeback_fixture()
    change_set = fixture.change_set
    clock = {"ms": 1_050_000}

    intent = WalletBusinessIntent(
        operation_id="pay",
        account_id="account",
        wallet_id="wallet",
        network_id="network",
        key_id="key",
        params={"amount": 100},
    )
    plan = wallet_plan_digest(intent)
    proof, trust = _signed(change_set, plan)
    envelope = WalletExecutionEnvelope(
        intent=intent,
        confirmation=WalletConfirmation(
            confirmed_by="actor", confirmed_at_ms=1_050_000, plan_digest=plan
        ),
        proof=proof,
    )
    gate = EnvelopeProofGate(
        envelope,
        tenant_id=change_set.tenant_id,
        actor_id="actor",
        resolver=_TrustResolver(trust),
        clock_ms=lambda: clock["ms"],
    )

    class _CountingProofGate:
        fresh = 0

        async def verify(self, change_set):
            type(self).fresh += 1
            return await gate.verify(change_set)

    verifier = DeterministicAuthorizationVerifier()
    verifier.grant(change_set)
    checks = {"n": 0}

    class _Rechecking:
        async def verify(self, change_set):
            checks["n"] += 1
            return await verifier.verify(change_set)

    service = _port(
        fixture,
        _CountingProofGate(),
        authorized=False,
        clock_ms=lambda: clock["ms"],
        verifier=_Rechecking(),
    )
    first = await service.apply(change_set)
    assert first.effect_status is WriteBackEffectStatus.APPLIED
    assert _CountingProofGate.fresh == 1
    assert await _status(fixture) == "approved"

    # Past the proof's expiry: a fresh proof would now be refused, but the
    # recorded effect returns the prior receipt without one, with
    # authorization still re-verified and no second effect.
    clock["ms"] = 1_200_000
    authorizations_before = checks["n"]
    second = await service.apply(change_set)
    assert second == first
    assert _CountingProofGate.fresh == 1
    assert checks["n"] > authorizations_before
    assert fixture.transport.attempts == 1

    # The gate refuses a confirmation of a different plan and a missing
    # proof before the verifier runs.
    wrong_plan = envelope.model_copy(
        update={
            "confirmation": WalletConfirmation(
                confirmed_by="actor", confirmed_at_ms=1_050_000, plan_digest="b" * 64
            )
        }
    )
    result = await EnvelopeProofGate(
        wrong_plan,
        tenant_id=change_set.tenant_id,
        actor_id="actor",
        resolver=_TrustResolver(trust),
        clock_ms=lambda: clock["ms"],
    ).verify(change_set)
    assert result.reason == "scope_mismatch"
    bare = envelope.model_copy(update={"proof": None})
    result = await EnvelopeProofGate(
        bare,
        tenant_id=change_set.tenant_id,
        actor_id="actor",
        resolver=_TrustResolver(trust),
        clock_ms=lambda: clock["ms"],
    ).verify(change_set)
    assert result.reason == "missing_signature"


def _expired(proof, trust):
    return _Gate(proof, lambda _: trust, now=1100)


def _revoked(proof, trust):
    return _Gate(proof, lambda _: replace(trust, revoked=True))


def _untrusted(proof, trust):
    return _Gate(proof, lambda _: None)


def _wrong_key_binding(proof, trust):
    return _Gate(
        proof, lambda _: replace(trust, binding=replace(trust.binding, key_id="x"))
    )


def _unsupported(proof, trust):
    return _Gate(proof.model_copy(update={"scheme": "other"}), lambda _: trust)


def _bad_signature(proof, trust):
    return _Gate(proof.model_copy(update={"signature": "A" * 86}), lambda _: trust)


def _wrong_scope(proof, trust):
    return _Gate(proof.model_copy(update={"tenant_id": "other"}), lambda _: trust)


def _unavailable(proof, trust):
    def boom(_):
        raise RuntimeError("sensitive upstream detail")

    return _Gate(proof, boom)


class _RefusedGate:
    """Returns a fixed refusal fact without running the verifier."""

    def __init__(self, reason):
        self.reason = reason

    async def verify(self, change_set):
        return WalletProofResult(outcome="refused", reason=self.reason)


def _missing(proof, trust):
    return _RefusedGate("missing_signature")


def _invalid_payload(proof, trust):
    return _Gate(proof.model_copy(update={"plan_digest": "Z" * 64}), lambda _: trust)


@pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R001.4")
@pytest.mark.parametrize(
    "make_gate,reason",
    [
        (_missing, "missing_signature"),
        (_invalid_payload, "invalid_payload"),
        (_expired, "expired"),
        (_revoked, "revoked_key"),
        (_untrusted, "untrusted_key"),
        (_wrong_key_binding, "untrusted_key"),
        (_unsupported, "unsupported_scheme"),
        (_bad_signature, "invalid_signature"),
        (_wrong_scope, "scope_mismatch"),
        (_unavailable, "verification_unavailable"),
    ],
)
async def test_every_proof_refusal_makes_no_effect(make_gate, reason):
    fixture = make_writeback_fixture()
    proof, trust = _signed(fixture.change_set)
    with pytest.raises(WalletProofRefusedError) as error:
        await _port(fixture, make_gate(proof, trust)).apply(fixture.change_set)
    assert error.value.reason == reason
    assert "sensitive" not in str(error.value)
    assert await _status(fixture) == "new"
    assert (
        not fixture.audit.reservations
        if hasattr(fixture.audit, "reservations")
        else True
    )


async def test_a_failing_gate_and_a_non_result_refuse_before_effect():
    fixture = make_writeback_fixture()

    class _Raises:
        async def verify(self, change_set):
            raise RuntimeError("down")

    class _NotAResult:
        async def verify(self, change_set):
            return {"outcome": "verified"}

    for gate, reason in (
        (_Raises(), "verification_unavailable"),
        (_NotAResult(), "missing_signature"),
    ):
        with pytest.raises(WalletProofRefusedError) as error:
            await _port(fixture, gate).apply(fixture.change_set)
        assert error.value.reason == reason
    assert await _status(fixture) == "new"


@pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R001.4")
async def test_replay_conflict_maps_idempotency_conflict():
    fixture = make_writeback_fixture()
    change_set = fixture.change_set
    # The durable connector reports the idempotency key as already bound
    # to a different effect.
    conflicting = WriteBackAttempt(
        tenant_id=change_set.tenant_id,
        change_set_id=change_set.change_set_id,
        change_set_digest="f" * 64,
        idempotency_key=change_set.idempotency_key,
        kind=WriteBackAttemptKind.APPLY,
        input_digest="3" * 64,
        output_digest="4" * 64,
        pre_source_version="v1",
        post_source_version="v2",
        applied_field_digest=change_set.patch_digest(),
        outcome=WriteBackOutcome.APPLIED,
        effect_status=WriteBackEffectStatus.APPLIED,
        connector_observation_digest="0" * 64,
        provenance_digest="0" * 64,
    )
    fixture.transport.effects[change_set.idempotency_key] = conflicting
    proof, trust = _signed(change_set)
    with pytest.raises(WalletProofRefusedError) as error:
        await _port(fixture, _Gate(proof, lambda _: trust)).apply(change_set)
    assert error.value.reason == "replay_conflict"
    assert await _status(fixture) == "new"
    assert fixture.transport.attempts == 0


class _WalletSink:
    domain = "wallet"
    enable_flag = "WALLET_ENABLE_WRITE"
    requires_wallet_proof = True

    def __init__(self) -> None:
        self.calls = 0

    def run(self, ctx, ops, *, dry_run):
        from scion_sdk.writeback_sinks.core import WritebackResult

        self.calls += 1
        return WritebackResult(target=self.domain)


def _wallet_grant():
    from scion_sdk.writeback_sinks.governed import SinkGrant

    return SinkGrant(
        target="wallet",
        enable_flag="WALLET_ENABLE_WRITE",
        write_enabled=True,
        approved=True,
    )


def test_wallet_sink_without_an_envelope_refuses_before_any_call(tmp_path):
    from scion_sdk.writeback_sinks.core import WritebackContext
    from scion_sdk.writeback_sinks.governed import execute_governed

    sink = _WalletSink()
    with pytest.raises(WalletProofRefusedError) as error:
        execute_governed(
            sink,
            WritebackContext(tenant_id="t", actor_id="actor"),
            {"payload": 1},
            grant=_wallet_grant(),
            dry_run=False,
            root=tmp_path,
        )
    assert error.value.reason == "missing_signature"
    assert sink.calls == 0


def test_wallet_sink_with_an_unsigned_envelope_refuses_before_any_call(tmp_path):
    from scion_sdk.writeback_sinks.core import WritebackContext
    from scion_sdk.writeback_sinks.governed import execute_governed

    intent = WalletBusinessIntent(
        operation_id="pay",
        account_id="account",
        wallet_id="wallet",
        network_id="network",
        key_id="key",
    )
    envelope = WalletExecutionEnvelope(
        intent=intent,
        confirmation=WalletConfirmation(
            confirmed_by="actor",
            confirmed_at_ms=1,
            plan_digest=wallet_plan_digest(intent),
        ),
    )
    sink = _WalletSink()
    with pytest.raises(WalletProofRefusedError) as error:
        execute_governed(
            sink,
            WritebackContext(tenant_id="t", actor_id="actor"),
            {"wallet_envelope": envelope},
            grant=_wallet_grant(),
            dry_run=False,
            root=tmp_path,
        )
    assert error.value.reason == "missing_signature"
    assert sink.calls == 0


class _TrustClient:
    """A structural double of the engine transport answering WalletKeyTrustGet."""

    def __init__(self, record):
        self.record = record
        self.sent = []

    async def _send(self, method, params, graph, *, idempotency_key):
        self.sent.append((method, params))
        return self.record


async def test_engine_resolver_reads_the_trust_record_for_the_callers_tenant():
    from scion_sdk.writeback.wallet_trust import EngineWalletKeyResolver

    binding = _binding(make_writeback_fixture().change_set)
    record = {
        "account_id": "account",
        "key_id": "key",
        "network_id": "network",
        "public_key": "ab" * 32,
        "signer_ref": "signer",
        "tenant_id": binding.tenant_id,
        "trust_revision": 3,
        "valid_from": "2026-01-01T00:00:00+00:00",
        "wallet_id": "wallet",
    }
    client = _TrustClient(record)
    trust = await EngineWalletKeyResolver(client).resolve(binding)
    assert trust is not None
    assert trust.trust_revision == "3" and trust.revoked is False
    assert client.sent[0][0] == "WalletKeyTrustGet"
    other = _TrustClient({**record, "tenant_id": "someone-else"})
    assert await EngineWalletKeyResolver(other).resolve(binding) is None
