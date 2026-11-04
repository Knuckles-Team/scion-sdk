"""SCION-CONNECTOR-CONTROL-R914.9: health and readiness per upstream."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP
from mcp.shared.exceptions import MCPError
from starlette.testclient import TestClient
from upstream_fixtures import clean_upstream_env, fixture_decl

from scion_sdk.connector_hooks import Readiness, serve_readiness
from scion_sdk.mcp.upstreams import REFUSAL_CODES, attach_upstreams
from scion_sdk.mcp.upstreams.health import (
    UPSTREAMS_RESOURCE_URI,
    UpstreamProber,
    UpstreamRegistry,
    compose_readiness,
)
from scion_sdk.mcp.upstreams.model import (
    UPSTREAM_SPAWN_FAILED,
    UpstreamError,
    UpstreamStatus,
)


def _ready_server(
    tmp_path: Path, strategy: str, flaky: bool
) -> tuple[FastMCP, UpstreamRegistry]:
    data, out, bad = tmp_path / "data", tmp_path / "out", tmp_path / "bad"
    for directory in (data, out, bad):
        directory.mkdir(parents=True, exist_ok=True)
    decls = (fixture_decl("alpha", data=data, out=out),)
    if flaky:
        decls = (*decls, fixture_decl("beta", data=data, out=bad, kind="flaky"))
    server = FastMCP(f"health-{strategy}-connector")
    registry = attach_upstreams(server, decls)
    assert registry is not None

    async def base() -> Readiness:
        return Readiness(ready=True, detail={})

    serve_readiness(server, compose_readiness(base, registry))
    return server, registry


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914.9")
async def test_inactive_is_not_failure_and_strategy_controls_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_upstream_env(monkeypatch)
    monkeypatch.setenv("SCION_UPSTREAMS_LIST_CACHE_TTL_S", "0")

    # No activation: ready, and every lazy upstream simply shows inactive.
    server, registry = _ready_server(tmp_path, "warn", flaky=False)
    with TestClient(server.http_app()) as http:
        response = http.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert all(entry["state"] == "inactive" for entry in body["upstreams"])

    # One upstream exits: warn keeps serving and shows it unavailable.
    server, registry = _ready_server(tmp_path, "warn", flaky=True)
    async with Client(server, mode="legacy") as client:
        await client.call_tool("registry_activate", {"namespace": "alpha"})
        await client.call_tool("registry_activate", {"namespace": "beta"})
        await client.list_tools()  # the flaky listing fails here
    with TestClient(server.http_app()) as http:
        response = http.get("/ready")
    assert response.status_code == 200
    states = {entry["namespace"]: entry for entry in response.json()["upstreams"]}
    assert states["alpha"]["state"] == "ready"
    assert states["beta"]["state"] == "unavailable"
    # The status carries refusal codes only, never upstream error text.
    for entry in states.values():
        if entry["last_error_code"] is not None:
            assert entry["last_error_code"] in REFUSAL_CODES
    assert isinstance(registry.status("beta"), UpstreamStatus)
    assert registry.status("beta").last_error_code is not None

    # The same failure under raise answers 503 and fails the list.
    monkeypatch.setenv("SCION_UPSTREAM_ERROR_STRATEGY", "raise")
    server, registry = _ready_server(tmp_path, "raise", flaky=True)
    async with Client(server, mode="legacy") as client:
        # The activation's own re-list carries the failure.
        with pytest.raises(MCPError):
            await client.call_tool("registry_activate", {"namespace": "beta"})
    with TestClient(server.http_app()) as http:
        response = http.get("/ready")
    assert response.status_code == 503
    assert registry.status("beta").state == "unavailable"

    # The registry's own resource carries the same block, keyed by the wire URI.
    async with Client(server, mode="legacy") as client:
        content = await client.read_resource(UPSTREAMS_RESOURCE_URI)
    assert {
        entry["namespace"] for entry in json.loads(content[0].text)["upstreams"]
    } == {
        "alpha",
        "beta",
    }

    # The prober refreshes active namespaces at most once per interval, and
    # a failed probe marks the upstream unavailable with a code, not text.
    decls = (fixture_decl("alpha", data=tmp_path / "data", out=tmp_path / "out"),)
    active_registry = UpstreamRegistry(
        decls, activation_setting="active", error_strategy="warn"
    )
    calls = 0

    async def probe() -> tuple[tuple[object, ...], None]:
        nonlocal calls
        calls += 1
        return ((), None)

    prober = UpstreamProber(active_registry, ((decls[0], probe),), interval_s=60)
    await prober.probe_due()
    await prober.probe_due()
    assert calls == 1

    async def failing_probe() -> tuple[tuple[object, ...], None]:
        raise UpstreamError(UPSTREAM_SPAWN_FAILED, "kaboom upstream error text")

    failing_prober = UpstreamProber(
        active_registry, ((decls[0], failing_probe),), interval_s=0
    )
    await failing_prober.probe_due()
    status = active_registry.status("alpha")
    assert status.state == "unavailable"
    assert status.last_error_code == "UPSTREAM_SPAWN_FAILED"
    assert "kaboom" not in json.dumps(status.model_dump())
