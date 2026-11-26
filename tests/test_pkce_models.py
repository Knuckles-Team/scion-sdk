"""PKCE models (SCION-CONNECTOR-CONTROL-R046.1)."""

from __future__ import annotations

import pytest
from credential_support import make_binding
from pkce_support import make_manager
from pydantic import ValidationError

from scion_sdk.auth.pkce import (
    PkceAuthConfig,
    PkceAuthResult,
    PkceChallenge,
    ProviderTokenProfile,
)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R046.1")
async def test_models_round_trip_and_results_hide_secrets() -> None:
    config = PkceAuthConfig(
        issuer="https://i.example.test", client_id="c", token_ref="r"
    )
    assert config.deadline_s == 120
    assert PkceAuthConfig.model_validate(config.model_dump()) == config
    for bad in (29, 601):
        with pytest.raises(ValidationError):
            PkceAuthConfig(
                issuer="https://i", client_id="c", token_ref="r", deadline_s=bad
            )
    profile = ProviderTokenProfile(access="opaque", refresh="none", identity="oidc")
    assert profile.access == "opaque" and profile.identity == "oidc"
    with pytest.raises(ValidationError):
        ProviderTokenProfile(access="saml")  # type: ignore[arg-type]
    manager, _, _, _ = make_manager()
    result = await manager.authenticate(
        make_binding(), operation_digest="d" * 64, interactive=True
    )
    assert set(PkceAuthResult.model_fields) == {
        "status",
        "token_ref",
        "receipt_ref",
        "challenge_ref",
    }
    assert "private" not in result.model_dump_json()
    challenge = PkceChallenge(
        binding_digest="a" * 64,
        client="c",
        operation_digest="d" * 64,
        challenge_ref="r",
        state="s",
        nonce="n",
        callback="http://127.0.0.1:1/cb",
        deadline_ms=1,
    )
    assert PkceChallenge.model_validate(challenge.model_dump()) == challenge


def test_ports_and_record_are_the_manager_contract() -> None:
    from pkce_support import Broker, Issuer, Transport

    from scion_sdk.auth.pkce import (
        PkceBroker,
        PkceChallengeTransport,
        PkceRecord,
        PkceTokenEndpoint,
    )

    issuer = Issuer()
    endpoint: PkceTokenEndpoint = issuer
    transport: PkceChallengeTransport = Transport(issuer)
    broker: PkceBroker = Broker()
    assert all(x is not None for x in (endpoint, transport, broker))
    assert set(PkceRecord.model_fields) == {
        "issuer_profile",
        "redirect_mode",
        "binding_digest",
        "challenge_ref",
        "outcome",
        "deadline_s",
        "token_ref",
    }
