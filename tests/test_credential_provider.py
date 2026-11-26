"""SCION-CONNECTOR-CONTROL-R035: a connector-scoped CredentialProvider."""

from __future__ import annotations

import logging

import pytest

from scion_sdk.credentials.provider import (
    CredentialProvider,
    CredentialScopeError,
)
from scion_sdk.credentials.references import parse_secret_reference


class _StubResolver:
    def resolve(self, reference: object) -> str:
        return "s3cr3t-value"


def test_resolves_env_and_in_scope_openbao_reference() -> None:
    provider = CredentialProvider(resolver=_StubResolver(), connector="freshrss-agent")
    env_ref = parse_secret_reference("env://FRESHRSS_TOKEN")
    assert provider.get_secret(env_ref) == "s3cr3t-value"

    bao_ref = parse_secret_reference("openbao://apps/freshrss-agent#TOKEN")
    assert provider.get_secret(bao_ref) == "s3cr3t-value"


def test_out_of_scope_openbao_reference_denied() -> None:
    provider = CredentialProvider(resolver=_StubResolver(), connector="freshrss-agent")
    other_ref = parse_secret_reference("openbao://apps/other-connector#TOKEN")
    with pytest.raises(CredentialScopeError):
        provider.get_secret(other_ref)


def test_resolved_value_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    provider = CredentialProvider(resolver=_StubResolver(), connector="freshrss-agent")
    with caplog.at_level(logging.DEBUG):
        value = provider.get_secret(parse_secret_reference("env://FRESHRSS_TOKEN"))
    assert value == "s3cr3t-value"
    assert "s3cr3t-value" not in caplog.text


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R035.4")
def test_refusal_reasons_precede_resolver_and_leak_nothing() -> None:
    from credential_support import make_binding

    ref = parse_secret_reference("openbao://apps/freshrss-agent#TOKEN")
    now = 1_000

    class _Counting(_StubResolver):
        calls = 0

        def resolve(self, reference: object) -> str:
            type(self).calls += 1
            return "s3cr3t-value"

    class _Authority:
        def __init__(self, verdict: str) -> None:
            self.verdict = verdict

        def verify(self, binding: object) -> str:
            if self.verdict == "boom":
                raise RuntimeError("s3cr3t-value")
            return self.verdict

    cases = [
        ("ok", make_binding(connector="other"), "binding_mismatch"),
        (
            "ok",
            make_binding(connector="freshrss-agent", expires_at_ms=now),
            "grant_expired",
        ),
        ("revoked", make_binding(connector="freshrss-agent"), "revoked"),
        ("scope_denied", make_binding(connector="freshrss-agent"), "scope_denied"),
        ("boom", make_binding(connector="freshrss-agent"), "authority_unavailable"),
    ]
    for verdict, binding, reason in cases:
        provider = CredentialProvider(
            resolver=_Counting(),
            connector="freshrss-agent",
            authority=_Authority(verdict),
            clock_ms=lambda: now,
        )
        with pytest.raises(PermissionError) as caught:
            provider.get_secret(ref, binding)
        assert getattr(caught.value, "reason", "") == reason
        assert "s3cr3t-value" not in str(caught.value)
        assert provider.records[-1].outcome == reason and _Counting.calls == 0
    env_denied = CredentialProvider(
        resolver=_Counting(),
        connector="freshrss-agent",
        authority=_Authority("revoked"),
        clock_ms=lambda: now,
    )
    with pytest.raises(PermissionError):
        env_denied.get_secret(
            parse_secret_reference("env://FRESHRSS_TOKEN"),
            make_binding(connector="freshrss-agent"),
        )
    assert _Counting.calls == 0
