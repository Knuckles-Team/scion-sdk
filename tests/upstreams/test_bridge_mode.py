"""SCION-CONNECTOR-CONTROL-R914.4: bridge mode attaches the proxy to a running desktop application."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scion_sdk.credentials.resolver import EnvironmentCredentialResolver
from scion_sdk.mcp.upstreams import (
    BridgeDecl,
    SandboxRoots,
    UpstreamDecl,
    UpstreamDeclError,
    UpstreamError,
)
from scion_sdk.mcp.upstreams.bridge import (
    build_bridge_command,
    is_loopback_address,
    remove_bridge_token,
)
from scion_sdk.mcp.upstreams.stdio import build_child_environment

TOKEN = "bridge-secret-token"  # sanitizer:ignore - synthetic test fixture, never a real credential


def _bridge_decl(address: str, token_ref: str = "env://BRIDGE_TOKEN") -> UpstreamDecl:
    return UpstreamDecl(
        name="studio",
        kind="bridge",
        command="/usr/bin/studio",
        args=("/app",),
        sandbox=SandboxRoots(
            read=("/srv/studio",),
            write=(),
            read_flag="--read-root",
            write_flag="--write-root",
        ),
        bridge=BridgeDecl(address=address, token_ref=token_ref),
    )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914.4")
def test_token_file_is_private_and_removed_and_remote_address_refuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("BRIDGE_TOKEN", TOKEN)
    resolver = EnvironmentCredentialResolver()
    run_root = tmp_path / "run"

    with caplog.at_level("DEBUG"):
        command, token_path = build_bridge_command(
            _bridge_decl("127.0.0.1:9400"), resolver=resolver, run_root=run_root
        )

    run_dir = token_path.parent
    assert oct(run_dir.stat().st_mode & 0o777) == "0o700"
    assert oct(token_path.stat().st_mode & 0o777) == "0o600"
    assert token_path.read_text(encoding="utf-8") == TOKEN
    joined = " ".join(command)
    assert TOKEN not in joined
    assert TOKEN not in caplog.text
    assert "--bridge 127.0.0.1:9400" in joined
    assert str(token_path) in command

    environment = build_child_environment(
        _bridge_decl("127.0.0.1:9400"), resolver=resolver, inherited=("PATH",)
    )
    assert TOKEN not in json.dumps(environment)

    remove_bridge_token(token_path)
    assert not token_path.exists()
    remove_bridge_token(token_path)  # again: tolerating an absent file
    remove_bridge_token(None)

    with pytest.raises(UpstreamError) as excinfo:
        build_bridge_command(
            _bridge_decl("10.1.2.3:80"), resolver=resolver, run_root=run_root
        )
    assert excinfo.value.code == "UPSTREAM_BRIDGE_NOT_LOOPBACK"
    assert TOKEN not in str(excinfo.value)

    unref = UpstreamDecl.model_construct(
        name="studio",
        kind="bridge",
        command="/usr/bin/studio",
        sandbox=SandboxRoots(
            read=("/srv/studio",),
            write=(),
            read_flag="--read-root",
            write_flag="--write-root",
        ),
        bridge=BridgeDecl(address="127.0.0.1:9400", token_ref=""),
    )
    with pytest.raises(UpstreamDeclError) as excinfo:
        build_bridge_command(unref, resolver=resolver, run_root=run_root)
    assert excinfo.value.code == "UPSTREAM_BRIDGE_TOKEN_MISSING"

    assert is_loopback_address("127.0.0.1:9400")
    assert is_loopback_address("::1:9400")
    assert not is_loopback_address("10.1.2.3:80")
    assert not is_loopback_address("127.0.0.1")
    assert not is_loopback_address("127.0.0.1:65536")
