"""The PKCE manager (SCION-CONNECTOR-CONTROL-R046.2, R046.4, R046.5)."""

from __future__ import annotations

import pytest
from credential_support import make_binding
from pkce_support import CODE, TOKEN, make_manager

from scion_sdk.auth.pkce import (
    REFUSAL_CODES,
    PkceAuthRefusedError,
    ProviderTokenProfile,
)

OP = "d" * 64


async def _run(manager, interactive: bool = True):  # type: ignore[no-untyped-def]
    return await manager.authenticate(
        make_binding(), operation_digest=OP, interactive=interactive
    )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R046.2")
async def test_s256_verifier_deadline_profile_and_persistence_port() -> None:
    manager, _issuer, transport, broker = make_manager()
    result = await _run(manager)
    assert result.status == "authenticated" and broker.persisted == ["vault:tok/1"]
    assert transport.timeouts == [120.0]  # the total deadline bounds the wait
    short, _, short_transport, _ = make_manager(deadline_s=30)
    await _run(short)
    assert short_transport.timeouts == [30.0]
    # a jwt profile rejects an opaque value; an opaque one invents no claims
    jwt, _, _, jwt_broker = make_manager(profile=ProviderTokenProfile(access="jwt"))
    with pytest.raises(PkceAuthRefusedError) as caught:
        await _run(jwt)
    assert caught.value.reason == "provider_unavailable" and jwt_broker.persisted == []
    # headless input is bounded and challenge-bound; interactive-less refuses
    refused, _, _, _ = make_manager()
    with pytest.raises(PkceAuthRefusedError) as caught:
        await _run(refused, interactive=False)
    assert caught.value.reason == "interaction_required"


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R046.4")
async def test_each_refusal_code_and_no_token_request_after_bad_state() -> None:
    seen: set[str] = set()

    async def expect(manager, reason: str, **kwargs):  # type: ignore[no-untyped-def]
        with pytest.raises(PkceAuthRefusedError) as caught:
            await _run(manager, **kwargs)
        assert caught.value.reason == reason
        assert TOKEN not in str(caught.value) and CODE not in str(caught.value)
        seen.add(reason)

    m, issuer, t, _ = make_manager()
    t.state_override = "forged"
    await expect(m, "state_mismatch")
    assert issuer.exchanges == 0  # no token request follows a bad state
    m, _, t, _ = make_manager()
    t.callback = "http://evil.example.test/cb"
    await expect(m, "insecure_redirect")
    m, _, t, _ = make_manager()
    t.launch_fails = True
    await expect(m, "browser_launch_failed")
    m, _, t, _ = make_manager()
    t.silent = True
    await expect(m, "input_timeout")
    clock = {"ms": 1_000_000}
    m, _, t, b = make_manager(now=clock)
    t.advance_ms = 121_000
    await expect(m, "expired")
    m, issuer, _, _ = make_manager()
    issuer.fail = True
    await expect(m, "provider_unavailable")
    m, _, _, b = make_manager()
    b.fail = True
    await expect(m, "persistence_failed")
    m, _, _, b = make_manager()
    b.no_receipt = True
    await expect(m, "persistence_failed")
    m, _, _, _ = make_manager()
    await expect(m, "interaction_required", interactive=False)
    m, issuer, _, _ = make_manager(
        profile=ProviderTokenProfile(introspection_required=True)
    )
    issuer.active = False
    await expect(m, "revoked")
    m, _, t, _ = make_manager()

    async def broken(challenge, timeout_s):  # type: ignore[no-untyped-def]
        raise OSError("down")

    t.receive = broken  # type: ignore[method-assign]
    await expect(m, "callback_unavailable")
    # callback replay: a challenge the broker already consumed is refused
    m, issuer, _, b = make_manager()
    b.deny_claim = True
    await expect(m, "expired")
    assert issuer.exchanges == 0
    # cancelled by the user at the provider
    m, _, t, _ = make_manager()

    async def denied(challenge, timeout_s):  # type: ignore[no-untyped-def]
        return f"{t.callback}?error=access_denied&state={t._state}"

    t.receive = denied  # type: ignore[method-assign]
    await expect(m, "cancelled")
    m, _, t, _ = make_manager()

    async def nocode(challenge, timeout_s):  # type: ignore[no-untyped-def]
        return f"{t.callback}?state={t._state}"

    t.receive = nocode  # type: ignore[method-assign]
    await expect(m, "no_credential")
    assert {"no_credential", "revoked", "expired", "cancelled"} <= seen
    assert seen <= REFUSAL_CODES
    with pytest.raises(ValueError):
        PkceAuthRefusedError("made_up")


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R046.5")
async def test_record_requires_persistence_receipt_and_hides_secrets() -> None:
    manager, _, _, _broker = make_manager()
    result = await _run(manager)
    (record,) = manager.records
    assert record.outcome == "persisted" and record.token_ref == "vault:tok/1"
    assert len(record.binding_digest) == 64 and record.deadline_s == 120
    assert record.challenge_ref == result.challenge_ref
    dumped = record.model_dump_json() + result.model_dump_json()
    assert CODE not in dumped and TOKEN not in dumped
    failing, _, _, fbroker = make_manager()
    fbroker.no_receipt = True
    with pytest.raises(PkceAuthRefusedError):
        await _run(failing)
    (failed,) = failing.records
    assert failed.outcome == "persistence_failed" and failed.token_ref == ""
