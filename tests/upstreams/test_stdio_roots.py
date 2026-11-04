"""SCION-CONNECTOR-CONTROL-R914.2: a stdio upstream declares its roots or it does not start."""

from __future__ import annotations

import pytest

from scion_sdk.mcp.upstreams import SandboxRoots, UpstreamDecl, UpstreamDeclError
from scion_sdk.mcp.upstreams.stdio import build_stdio_command


def _decl(sandbox: SandboxRoots | None) -> UpstreamDecl:
    return UpstreamDecl(
        name="alpha", command="/usr/bin/alpha", args=("/app",), sandbox=sandbox
    )


def _roots(read: tuple[str, ...] = (), write: tuple[str, ...] = ()) -> SandboxRoots:
    return SandboxRoots(
        read=read, write=write, read_flag="--read-root", write_flag="--write-root"
    )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914.2")
def test_missing_roots_refuse_and_empty_roots_pass_no_flag() -> None:
    with pytest.raises(UpstreamDeclError) as excinfo:
        build_stdio_command(_decl(None))
    assert excinfo.value.code == "UPSTREAM_ROOTS_REQUIRED"

    command = build_stdio_command(_decl(_roots()))
    assert command == ["/usr/bin/alpha", "/app"]

    command = build_stdio_command(
        _decl(_roots(read=("/srv/one", "/srv/two"), write=("/var/one",)))
    )
    assert command == [
        "/usr/bin/alpha",
        "/app",
        "--read-root",
        "/srv/one",
        "--read-root",
        "/srv/two",
        "--write-root",
        "/var/one",
    ]

    with pytest.raises(UpstreamDeclError) as excinfo:
        build_stdio_command(_decl(_roots(read=("relative/path",))))
    assert excinfo.value.code == "UPSTREAM_ROOT_UNSAFE"

    with pytest.raises(UpstreamDeclError) as excinfo:
        build_stdio_command(_decl(_roots(read=("/",))))
    assert excinfo.value.code == "UPSTREAM_ROOT_UNSAFE"

    with pytest.raises(UpstreamDeclError) as excinfo:
        build_stdio_command(
            _decl(_roots(read=("/srv/data",), write=("/srv/data/sub",)))
        )
    assert excinfo.value.code == "UPSTREAM_ROOT_OVERLAP"
