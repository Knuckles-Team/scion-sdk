"""SCION-CONNECTOR-CONTROL-R914.1: the upstream declaration model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from upstream_fixtures import refusal_code

from scion_sdk.mcp.upstreams import (
    REFUSAL_CODES,
    UpstreamDecl,
    UpstreamDeclError,
    is_reserved_namespace,
    validate_declarations,
)


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "alpha",
        "command": "/usr/bin/alpha",
        "args": ("/app/alpha",),
        "sandbox": {
            "read": ("/srv/alpha",),
            "write": ("/var/alpha",),
            "read_flag": "--read-root",
            "write_flag": "--write-root",
        },
    }
    payload.update(overrides)
    return payload


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914.1")
def test_decl_round_trips_and_refuses_wrong_kind_fields() -> None:
    decl = UpstreamDecl(
        **_payload(
            activation="lazy",
            env={"TOKEN": "env://ALPHA_TOKEN"},
            pool_scope="server",
            expected_fingerprint="ab" * 32,
            on_drift="refuse",
        )
    )
    assert UpstreamDecl.model_validate(decl.model_dump()) == decl
    assert decl.effective_namespace == "alpha"
    assert decl.effective_pool_scope == "server"

    http = UpstreamDecl(
        name="ledger",
        kind="http",
        url="https://127.0.0.1:8443",
        credential="env://LEDGER_TOKEN",
    )
    assert UpstreamDecl.model_validate(http.model_dump()) == http
    assert http.effective_pool_scope == "principal"

    assert "UPSTREAM_TOOL_COLLISION" in REFUSAL_CODES
    assert is_reserved_namespace("registry") and is_reserved_namespace("local")
    assert not is_reserved_namespace("alpha")

    with pytest.raises((UpstreamDeclError, ValidationError)) as excinfo:
        UpstreamDecl(**_payload(url="https://alpha.example.com"))
    assert refusal_code(excinfo) == "UPSTREAM_KIND_FIELDS"

    with pytest.raises((UpstreamDeclError, ValidationError)) as excinfo:
        UpstreamDecl(**_payload(name="Photo_1"))
    assert refusal_code(excinfo) == "UPSTREAM_NAMESPACE_INVALID"

    with pytest.raises((UpstreamDeclError, ValidationError)) as excinfo:
        UpstreamDecl(**_payload(name="registry"))
    assert refusal_code(excinfo) == "UPSTREAM_NAMESPACE_INVALID"

    with pytest.raises(UpstreamDeclError) as excinfo:
        validate_declarations(
            (
                UpstreamDecl(**_payload(name="one", namespace="shared")),
                UpstreamDecl(**_payload(name="two", namespace="shared")),
            )
        )
    assert excinfo.value.code == "UPSTREAM_NAMESPACE_DUPLICATE"

    with pytest.raises(UpstreamDeclError) as excinfo:
        validate_declarations(
            (decl, UpstreamDecl(**_payload(name="alpha", namespace="beta")))
        )
    assert excinfo.value.code == "UPSTREAM_DECL_INVALID"
