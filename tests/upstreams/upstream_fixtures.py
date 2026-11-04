"""Shared helpers for the upstream registry tests (SCION-CONNECTOR-CONTROL-R914)."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from scion_sdk.mcp.upstreams import SandboxRoots, UpstreamDecl, UpstreamDeclError

#: The fixture upstream the tests spawn as stdio children.
FIXTURE_SCRIPT = Path(__file__).parent / "upstream_fixture.py"
READ_FLAG = "--fixture-read-root"
WRITE_FLAG = "--fixture-write-root"

#: Every registry knob, so a test starts from the defaults whatever the shell held.
UPSTREAM_ENV_VARS = (
    "SCION_UPSTREAMS_FILE",
    "SCION_UPSTREAMS_DISABLED",
    "SCION_UPSTREAMS_CONNECT_TIMEOUT_S",
    "SCION_UPSTREAMS_CALL_TIMEOUT_S",
    "SCION_UPSTREAMS_LIST_CACHE_TTL_S",
    "SCION_UPSTREAMS_INHERIT_ENV",
    "SCION_UPSTREAMS_MAX_ACTIVE_NAMESPACES",
    "SCION_UPSTREAMS_IDLE_TTL_S",
    "SCION_UPSTREAMS_MAX_PROCESSES",
    "SCION_UPSTREAMS_STOP_GRACE_S",
    "SCION_UPSTREAMS_RESTART_LIMIT",
    "SCION_UPSTREAMS_RESTART_WINDOW_S",
    "SCION_UPSTREAMS_CIRCUIT_COOLDOWN_S",
    "SCION_UPSTREAMS_PROBE_INTERVAL_S",
    "SCION_UPSTREAM_ACTIVATION",
    "SCION_UPSTREAM_ERROR_STRATEGY",
)


def clean_upstream_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The registry's knobs read their defaults, whatever the environment held."""
    for name in UPSTREAM_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def refusal_code(excinfo: pytest.ExceptionInfo) -> str:
    """The registry refusal code behind the exception.

    A plain function raises :class:`UpstreamDeclError` directly; a model
    validator raises through pydantic, which wraps it in a ``ValidationError``
    carrying the original error in each entry's context.
    """
    error = excinfo.value
    if isinstance(error, UpstreamDeclError):
        return error.code
    if isinstance(error, ValidationError):
        for entry in error.errors():
            inner = (entry.get("ctx") or {}).get("error")
            if isinstance(inner, UpstreamDeclError):
                return inner.code
    raise AssertionError(f"no refusal code behind {error!r}")


def fixture_decl(
    name: str,
    *,
    data: Path,
    out: Path,
    kind: str = "tools",
    **kwargs: object,
) -> UpstreamDecl:
    """A stdio declaration of the fixture upstream under the given roots."""
    return UpstreamDecl(
        name=name,
        command=sys.executable,
        args=[str(FIXTURE_SCRIPT), "--fixture-kind", kind],
        sandbox=SandboxRoots(
            read=(str(data),),
            write=(str(out),),
            read_flag=READ_FLAG,
            write_flag=WRITE_FLAG,
        ),
        **kwargs,
    )


def startup_marker(out: Path, kind: str) -> Path:
    """The marker the fixture child writes at startup, holding its pid."""
    return out / f"started-{kind}"


def read_pid(path: Path) -> str | None:
    """The pid recorded in a startup marker, or ``None`` when it was never written."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


async def process_gone(pid: int) -> bool:
    """Poll briefly until the process no longer exists."""
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        await asyncio.sleep(0.1)
    return False
