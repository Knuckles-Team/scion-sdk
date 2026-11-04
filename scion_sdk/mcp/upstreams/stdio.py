"""Build a stdio child's command line and clean environment (R914.2, R914.3).

The command line carries the declared command and args, then one ``flag path``
pair per sandbox root. A stdio child gets a clean environment: only the names
in ``inherit_env`` plus the declared ``env`` entries, each resolved from its
secret reference. The SDK never widens a root.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import PurePath
from typing import TYPE_CHECKING

from scion_sdk.credentials.references import (
    SecretReferenceError,
    parse_secret_reference,
)
from scion_sdk.mcp.upstreams.model import (
    UPSTREAM_KIND_FIELDS,
    UPSTREAM_ROOT_OVERLAP,
    UPSTREAM_ROOT_UNSAFE,
    UPSTREAM_ROOTS_REQUIRED,
    UPSTREAM_SECRET_LITERAL,
    UPSTREAM_SPAWN_FAILED,
    UpstreamDecl,
    UpstreamDeclError,
)

if TYPE_CHECKING:
    from scion_sdk.credentials.resolver import CredentialResolver

__all__ = ["build_child_environment", "build_stdio_command"]


def _root_problem(root: str) -> str | None:
    """Why ``root`` is unsafe, or ``None`` when it is a bounded grant.

    A root must be absolute, not ``/`` and not a filesystem root.
    """
    path = PurePath(root)
    if not path.is_absolute():
        return f"{root!r} is not absolute"
    if len(path.parts) < 2:
        return f"{root!r} is a filesystem root"
    return None


def _roots_collide(read: Sequence[str], write: Sequence[str]) -> bool:
    """Whether a read root doubles as (or nests) a write root."""
    for read_root in read:
        read_path = PurePath(read_root)
        for write_root in write:
            write_path = PurePath(write_root)
            if (
                read_path == write_path
                or read_path.is_relative_to(write_path)
                or write_path.is_relative_to(read_path)
            ):
                return True
    return False


def build_stdio_command(decl: UpstreamDecl) -> list[str]:
    """The child's full command line, roots validated before any spawn (R914.2).

    Raises:
        UpstreamDeclError: ``UPSTREAM_ROOTS_REQUIRED`` with no sandbox,
            ``UPSTREAM_ROOT_UNSAFE`` for a relative or root path,
            ``UPSTREAM_ROOT_OVERLAP`` when read and write roots collide.
    """
    sandbox = decl.sandbox
    if sandbox is None:
        raise UpstreamDeclError(
            UPSTREAM_ROOTS_REQUIRED, f"{decl.name} declares no sandbox"
        )
    if not decl.command:
        raise UpstreamDeclError(UPSTREAM_KIND_FIELDS, f"{decl.name} has no command")
    for root in (*sandbox.read, *sandbox.write):
        problem = _root_problem(root)
        if problem is not None:
            raise UpstreamDeclError(UPSTREAM_ROOT_UNSAFE, problem)
    if _roots_collide(sandbox.read, sandbox.write):
        raise UpstreamDeclError(
            UPSTREAM_ROOT_OVERLAP, f"{decl.name} read and write roots collide"
        )
    command = [decl.command, *decl.args]
    for root in sandbox.read:
        command.extend([sandbox.read_flag, root])
    for root in sandbox.write:
        command.extend([sandbox.write_flag, root])
    return command


def build_child_environment(
    decl: UpstreamDecl,
    *,
    resolver: CredentialResolver,
    inherited: Mapping[str, str] | Sequence[str],
) -> dict[str, str]:
    """A clean child environment: inherited names plus declared references (R914.3).

    Args:
        decl: The upstream declaration.
        resolver: Resolves each declared ``env`` secret reference.
        inherited: The names to inherit, as a name set or a name-to-value map.
            Only names present here (and in the parent) are passed through.

    Raises:
        UpstreamDeclError: ``UPSTREAM_SECRET_LITERAL`` for a non-reference env
            value; ``UPSTREAM_SPAWN_FAILED`` when a reference cannot resolve.
    """
    names = inherited.keys() if isinstance(inherited, Mapping) else inherited
    environment = {name: value for name, value in os.environ.items() if name in names}
    for key, reference in sorted(decl.env.items()):
        try:
            parsed = parse_secret_reference(reference)
        except SecretReferenceError:
            raise UpstreamDeclError(
                UPSTREAM_SECRET_LITERAL, f"{decl.name} env {key!r} must be a reference"
            ) from None
        try:
            environment[key] = resolver.resolve(parsed)
        except Exception:
            raise UpstreamDeclError(
                UPSTREAM_SPAWN_FAILED,
                f"{decl.name} env {key!r} reference is unavailable",
            ) from None
    return environment
