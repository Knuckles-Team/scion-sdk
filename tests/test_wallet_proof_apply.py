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
from tests.test_wallet_proof_served import world as _world  # noqa: F401  (fixture)

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
    domain = "walletfake"
    enable_flag = "WALLETFAKE_ENABLE_WRITE"
    requires_wallet_proof = True

    def __init__(self):
        self.calls = []

    def run(self, ctx, ops, *, dry_run):
        from scion_sdk.writeback_sinks import core

        self.calls.append(dry_run)
        return core.WritebackResult(target=self.domain)


def _wallet_ops(with_proof):
    fixture = make_writeback_fixture()
    intent = WalletBusinessIntent(
        operation_id="pay",
        account_id="account",
        wallet_id="wallet",
        network_id="network",
        key_id="key",
        params={"amount": 100},
    )
    plan = wallet_plan_digest(intent)
    proof = _signed(fixture.change_set, plan)[0] if with_proof else None
    envelope = WalletExecutionEnvelope(
        intent=intent,
        confirmation=WalletConfirmation(
            confirmed_by="actor", confirmed_at_ms=1_050_000, plan_digest=plan
        ),
        proof=proof,
    )
    return {"creations": [{"name": "a"}], "wallet_envelope": envelope}


@pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R001.3")
def test_run_writeback_passes_the_gate_for_a_wallet_sink(tmp_path):
    from scion_sdk.engine_capability import (
        EngineCapabilityUnavailable,
        engine_capability_present,
    )
    from scion_sdk.writeback_sinks import core
    from scion_sdk.writeback_sinks.governed import (
        SinkGrant,
        _proof_gate,
        execute_governed,
    )

    sink = _WalletSink()
    grant = SinkGrant(
        target=sink.domain, enable_flag=sink.enable_flag, write_enabled=True
    )
    ctx = core.WritebackContext(tenant_id="tenant", actor_id="actor")

    class _Plain:
        domain, enable_flag = "plainfake", "PLAINFAKE_ENABLE_WRITE"

    assert _proof_gate(_Plain(), ctx, {"wallet_envelope": object()}) is None
    assert isinstance(
        _proof_gate(sink, ctx, _wallet_ops(with_proof=True)), EnvelopeProofGate
    )

    bare = {"creations": [{"name": "a"}]}
    with pytest.raises(WalletProofRefusedError) as missing:
        execute_governed(sink, ctx, bare, grant=grant, dry_run=False, root=tmp_path)
    assert missing.value.reason == "missing_signature"

    if not engine_capability_present("WalletKeyTrustGet"):
        with pytest.raises(EngineCapabilityUnavailable) as caught:
            execute_governed(
                sink,
                ctx,
                _wallet_ops(with_proof=True),
                grant=grant,
                dry_run=False,
                root=tmp_path,
            )
        assert caught.value.method == "WalletKeyTrustGet"
    assert sink.calls == []


async def test_engine_resolver_maps_the_trust_record_under_the_callers_tenant(
    monkeypatch,
):
    import importlib
    from types import SimpleNamespace

    from scion_sdk.writeback.wallet_trust import EngineWalletKeyResolver

    compute = importlib.import_module("medulla.generated.compute")
    record = SimpleNamespace(
        tenant_id="tenant",
        key_id="key",
        public_key=(b"k" * 32).hex(),
        signer_ref="signer",
        trust_revision=3,
        valid_from="1970-01-01T00:15:00Z",
        valid_until=None,
        revoked=None,
    )

    async def fake(client, params):
        assert params == {
            "account_id": "account",
            "network_id": "network",
            "wallet_id": "wallet",
        }
        return record

    monkeypatch.setattr(compute, "send_wallet_key_trust_get", fake, raising=False)
    fixture = make_writeback_fixture()
    binding = replace(_binding(fixture.change_set), tenant_id="tenant")
    trust = await EngineWalletKeyResolver(object()).resolve(binding)
    assert trust is not None
    assert (trust.signer_ref, trust.trust_revision, trust.valid_from) == (
        "signer",
        "3",
        900,
    )
    assert trust.revoked is False
    foreign = replace(binding, tenant_id="other")
    assert await EngineWalletKeyResolver(object()).resolve(foreign) is None


@pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R001.5")
def test_evidence_is_joined_redacted_and_survives_restart(tmp_path, caplog, request):
    import json

    served_world = request.getfixturevalue("_world")

    from scion_sdk.writeback.wallet_gate import proof_evidence
    from scion_sdk.writeback_sinks.governed import PROOF_EVIDENCE_FILE
    from scion_sdk.writeback_sinks.wallet_prepare import PREPARATION_KEY
    from tests.test_wallet_proof_served import _prepared, _run, _Sink

    ops = _prepared(served_world)
    sink = _Sink()
    with caplog.at_level("DEBUG"):
        outcome = _run(sink, ops, tmp_path)
    preparation = ops[PREPARATION_KEY]
    path = tmp_path / preparation.change_set_id / PROOF_EVIDENCE_FILE
    # "Restart": the evidence is read from the durable ledger, not from process state.
    text = path.read_text(encoding="utf-8")
    record = json.loads(text)
    assert set(record) == {
        "payload_digest",
        "plan_digest",
        "change_set_digest",
        "tenant",
        "actor",
        "connector",
        "operation",
        "network",
        "signer_ref",
        "trust_revision",
        "verified_at_ms",
    }
    assert record["change_set_digest"] == preparation.change_set_digest
    assert record["plan_digest"] == preparation.plan_digest
    assert record["tenant"] == "tenant" and record["operation"] == "pay"
    assert outcome.attempt is not None
    signature = ops["wallet_envelope"].proof.signature
    assert signature not in text and signature not in caplog.text
    assert "public_key" not in text and "signature" not in text
    with pytest.raises(ValueError):
        from scion_sdk.writeback.wallet_proof import WalletProofResult

        proof_evidence(
            _binding(outcome.change_set),
            WalletProofResult(outcome="refused", reason="expired"),
            verified_at_ms=1,
        )
