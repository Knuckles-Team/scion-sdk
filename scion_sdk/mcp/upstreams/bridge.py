"""Bridge mode: attach the proxy to a running desktop application (R914.4).

A bridge upstream runs like stdio but also passes a loopback ``address`` and a
token file. The token is resolved from its reference and written to a file with
mode 0600 inside a private run directory with mode 0700; the token value never
appears on the command line, in the environment, or in a log. The loader
removes the file when the process stops.
"""

from __future__ import annotations

import os
from ipaddress import ip_address
from pathlib import Path
from typing import TYPE_CHECKING

from scion_sdk.credentials.references import (
    SecretReferenceError,
    parse_secret_reference,
)
from scion_sdk.file_lock import lock_exclusive, unlock
from scion_sdk.mcp.upstreams.model import (
    UPSTREAM_BRIDGE_NOT_LOOPBACK,
    UPSTREAM_BRIDGE_TOKEN_MISSING,
    UPSTREAM_KIND_FIELDS,
    UPSTREAM_SPAWN_FAILED,
    UpstreamDecl,
    UpstreamDeclError,
    UpstreamError,
)
from scion_sdk.mcp.upstreams.stdio import build_stdio_command

if TYPE_CHECKING:
    from scion_sdk.credentials.resolver import CredentialResolver

__all__ = ["build_bridge_command", "is_loopback_address"]

_TOKEN_FILE_NAME = "control-token"  # nosec B105 - token file name constant, not a credential
_LOCK_FILE_NAME = ".bridge-lock"
RUN_DIR_MODE = 0o700
TOKEN_FILE_MODE = 0o600


def is_loopback_address(address: str) -> bool:
    """Whether ``address`` is a loopback host with a valid port."""
    host, separator, port = address.rpartition(":")
    if not separator or not host or not port.isdigit():
        return False
    if not 1 <= int(port) <= 65535:
        return False
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def build_bridge_command(
    decl: UpstreamDecl,
    *,
    resolver: CredentialResolver,
    run_root: Path,
) -> tuple[list[str], Path]:
    """The bridge child's command line and its private token file (R914.4).

    The token is resolved and written under ``run_root/<name>/`` with mode 0600
    in a 0700 directory. The returned token path is what the loader removes
    when the process stops.

    Raises:
        UpstreamError: ``UPSTREAM_BRIDGE_NOT_LOOPBACK`` for a non-loopback
            address; ``UPSTREAM_SPAWN_FAILED`` when the token reference cannot
            resolve.
        UpstreamDeclError: ``UPSTREAM_BRIDGE_TOKEN_MISSING`` without a
            ``token_ref``; ``UPSTREAM_KIND_FIELDS`` without a bridge block.
    """
    bridge = decl.bridge
    if bridge is None:
        raise UpstreamDeclError(
            UPSTREAM_KIND_FIELDS, f"{decl.name}: bridge requires bridge"
        )
    if not is_loopback_address(bridge.address):
        raise UpstreamError(
            UPSTREAM_BRIDGE_NOT_LOOPBACK, f"{decl.name}: {bridge.address!r}"
        )
    if not bridge.token_ref:
        raise UpstreamDeclError(
            UPSTREAM_BRIDGE_TOKEN_MISSING, f"{decl.name} has no token_ref"
        )
    try:
        parsed = parse_secret_reference(bridge.token_ref)
    except SecretReferenceError:
        raise UpstreamDeclError(
            UPSTREAM_SPAWN_FAILED, f"{decl.name} token_ref is not a reference"
        ) from None
    try:
        token = resolver.resolve(parsed)
    except Exception:
        raise UpstreamError(
            UPSTREAM_SPAWN_FAILED, f"{decl.name} token reference is unavailable"
        ) from None

    run_dir = run_root / decl.name
    run_dir.mkdir(mode=RUN_DIR_MODE, parents=True, exist_ok=True)
    os.chmod(run_dir, RUN_DIR_MODE)
    token_path = run_dir / _TOKEN_FILE_NAME
    lock_path = run_dir / _LOCK_FILE_NAME
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, TOKEN_FILE_MODE)
    try:
        lock_exclusive(lock_fd)
        with open(token_path, "w", encoding="utf-8") as handle:
            handle.write(token)
        os.chmod(token_path, TOKEN_FILE_MODE)
    finally:
        unlock(lock_fd)
        os.close(lock_fd)

    command = build_stdio_command(decl)
    command.extend([bridge.address_flag, bridge.address])
    command.extend([bridge.token_file_flag, str(token_path)])
    return command, token_path


def remove_bridge_token(token_path: Path | None) -> None:
    """Remove a materialized token file, tolerating an absent file."""
    if token_path is None:
        return
    try:
        token_path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        pass
