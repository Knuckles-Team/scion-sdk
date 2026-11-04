"""SCION-CONNECTOR-CONTROL-R914.3: credentials, environment and TLS arrive by reference only."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from upstream_fixtures import refusal_code

from scion_sdk.credentials.resolver import EnvironmentCredentialResolver
from scion_sdk.mcp.upstreams import (
    SandboxRoots,
    UpstreamDecl,
    UpstreamDeclError,
    UpstreamError,
)
from scion_sdk.mcp.upstreams.health import UpstreamRegistry
from scion_sdk.mcp.upstreams.http import build_http_transport
from scion_sdk.mcp.upstreams.stdio import build_child_environment

SECRET = "s3cr3t-token-value"  # sanitizer:ignore - synthetic test fixture, never a real credential
LEDGER = "https://127.0.0.1:8443"


def _sandbox() -> SandboxRoots:
    return SandboxRoots(
        read=("/srv/alpha",),
        write=(),
        read_flag="--read-root",
        write_flag="--write-root",
    )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914.3")
def test_secrets_never_reach_logs_or_status_and_literals_refuse(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("UPSTREAM_TOKEN", SECRET)
    resolver = EnvironmentCredentialResolver()
    decl = UpstreamDecl(
        name="ledger", kind="http", url=LEDGER, credential="env://UPSTREAM_TOKEN"
    )

    with caplog.at_level("DEBUG"):
        transport = build_http_transport(decl, resolver=resolver)
    assert transport.headers == {"Authorization": f"Bearer {SECRET}"}
    assert SECRET not in caplog.text
    assert SECRET not in str(decl)
    assert SECRET not in json.dumps(decl.model_dump())

    registry = UpstreamRegistry(
        (decl,), activation_setting="lazy", error_strategy="warn"
    )
    assert SECRET not in json.dumps(registry.readiness_block())

    with pytest.raises(UpstreamError) as excinfo:
        build_http_transport(
            UpstreamDecl(
                name="ledger", kind="http", url=LEDGER, credential="env://NOT_SET_REF"
            ),
            resolver=resolver,
        )
    assert excinfo.value.code == "UPSTREAM_EGRESS_REFUSED"
    assert SECRET not in str(excinfo.value)

    with pytest.raises((UpstreamDeclError, ValidationError)) as excinfo:
        UpstreamDecl(
            name="alpha",
            command="/usr/bin/alpha",
            sandbox=_sandbox(),
            env={"TOKEN": SECRET},
        )
    assert refusal_code(excinfo) == "UPSTREAM_SECRET_LITERAL"
    # The registry's own refusal message carries no secret (pydantic echoes the
    # caller's input dict in its repr; that is the caller's own data).
    if isinstance(excinfo.value, ValidationError):
        message = "".join(
            str(entry.get("ctx", {}).get("error")) for entry in excinfo.value.errors()
        )
    else:
        message = str(excinfo.value)
    assert SECRET not in message

    with pytest.raises(UpstreamError) as excinfo:
        build_http_transport(
            UpstreamDecl(name="ledger", kind="http", url="http://10.1.2.3:80"),
            resolver=resolver,
        )
    assert excinfo.value.code == "UPSTREAM_EGRESS_REFUSED"

    with pytest.raises(UpstreamError) as excinfo:
        build_http_transport(
            UpstreamDecl(name="ledger", kind="http", url="ftp://127.0.0.1:80"),
            resolver=resolver,
        )
    assert excinfo.value.code == "UPSTREAM_EGRESS_REFUSED"

    monkeypatch.setenv("PARENT_ONLY_VAR", "parent-value")
    monkeypatch.setenv("CHILD_TOKEN", "child-token")
    child = UpstreamDecl(
        name="alpha",
        command="/usr/bin/alpha",
        sandbox=_sandbox(),
        env={"TOKEN": "env://CHILD_TOKEN"},
    )
    environment = build_child_environment(child, resolver=resolver, inherited=("PATH",))
    assert environment["TOKEN"] == "child-token"
    assert "PARENT_ONLY_VAR" not in environment
    assert SECRET not in json.dumps(environment)
