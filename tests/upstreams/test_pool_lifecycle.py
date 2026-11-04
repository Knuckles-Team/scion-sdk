"""SCION-CONNECTOR-CONTROL-R914.8: stdio upstreams run in a shared pool, not per session."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP
from upstream_fixtures import clean_upstream_env, fixture_decl, process_gone

from scion_sdk.credentials.resolver import EnvironmentCredentialResolver
from scion_sdk.mcp.upstreams import attach_upstreams
from scion_sdk.mcp.upstreams.elicitation import make_elicitation_forwarder
from scion_sdk.mcp.upstreams.model import UpstreamError
from scion_sdk.mcp.upstreams.pool import PoolSettings, UpstreamPool, current_principal


def _pool(tmp_path: Path, **overrides: object) -> UpstreamPool:
    return UpstreamPool(
        settings=PoolSettings(**overrides),
        run_root=tmp_path / "run",
        resolver=EnvironmentCredentialResolver(),
        inherited_env=("PATH", "HOME", "LANG"),
        elicitation_forwarder=make_elicitation_forwarder(),
    )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914.8")
async def test_one_process_serves_many_sessions_and_restart_and_idle_reap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_upstream_env(monkeypatch)
    monkeypatch.setenv("SCION_UPSTREAMS_LIST_CACHE_TTL_S", "0")
    data, out = tmp_path / "data", tmp_path / "out"
    data.mkdir()
    out.mkdir()
    decl = fixture_decl("alpha", data=data, out=out)

    # Three concurrent sessions must share one pooled process.
    server = FastMCP("pool-connector")
    attach_upstreams(server, (decl,))
    state = {"open": 0}
    release = asyncio.Event()

    async def session_pid() -> str:
        async with Client(server, mode="legacy") as client:
            await client.call_tool("registry_activate", {"namespace": "alpha"})
            pid = str((await client.call_tool("alpha_whoami", {})).data)
            state["open"] += 1
            if state["open"] == 3:
                release.set()
            await release.wait()
        return pid

    pids = await asyncio.gather(session_pid(), session_pid(), session_pid())
    assert len(set(pids)) == 1, "three sessions must share one pooled process"

    # A killed child restarts on the next use, and four restarts within the
    # window open the circuit.
    pool = _pool(tmp_path)
    client = await pool.acquire(decl, "system")
    async with client:
        pid = int(str((await client.call_tool("whoami", {})).data))
    for _ in range(4):
        os.kill(pid, signal.SIGKILL)
        await asyncio.sleep(0.5)
        async with client:
            pid = int(str((await client.call_tool("whoami", {})).data))
    assert pool.restarts_total(decl.name) == 4
    assert pool.is_circuit_open(decl.name)
    with pytest.raises(UpstreamError, match="UPSTREAM_CIRCUIT_OPEN"):
        await pool.acquire(decl, "system")

    # Idle past the ttl, the child stops.
    idle_pool = _pool(tmp_path)
    client = await idle_pool.acquire(decl, "system")
    async with client:
        reap_pid = int(str((await client.call_tool("whoami", {})).data))
    entry = idle_pool._entries[decl.name]
    entry.last_used -= PoolSettings().idle_ttl_s + 1
    await idle_pool.reap_idle()
    assert decl.name not in idle_pool._entries
    assert await process_gone(reap_pid)

    # Under principal scope, two principals get two processes; the server
    # scope pools by name alone.
    principal_pool = _pool(tmp_path)
    assert principal_pool.pool_key(decl, "alice", scope="principal") == "alpha:alice"
    assert principal_pool.pool_key(decl, "bob") == decl.name
    client_a = await principal_pool.acquire(decl, "alice", scope="principal")
    client_b = await principal_pool.acquire(decl, "bob", scope="principal")
    async with client_a:
        pid_a = int(str((await client_a.call_tool("whoami", {})).data))
    async with client_b:
        pid_b = int(str((await client_b.call_tool("whoami", {})).data))
    assert pid_a != pid_b

    assert current_principal() == "system"
