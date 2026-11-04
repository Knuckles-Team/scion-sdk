"""SCION-CONNECTOR-CONTROL-R914.7: namespaces start hidden; activation is per session."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from upstream_fixtures import clean_upstream_env, fixture_decl, startup_marker

from scion_sdk.mcp.upstreams import attach_upstreams
from scion_sdk.mcp.upstreams.gate import session_active_namespaces


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914.7")
async def test_activation_is_per_session_lazy_and_capped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_upstream_env(monkeypatch)
    monkeypatch.setenv("SCION_UPSTREAMS_LIST_CACHE_TTL_S", "0")
    monkeypatch.setenv("SCION_UPSTREAMS_MAX_ACTIVE_NAMESPACES", "2")
    data, out, out2, out3 = (
        tmp_path / "data",
        tmp_path / "out",
        tmp_path / "out2",
        tmp_path / "out3",
    )
    for directory in (data, out, out2, out3):
        directory.mkdir()
    alpha = fixture_decl("alpha", data=data, out=out)
    beta = fixture_decl("beta", data=data, out=out2)
    epsilon = fixture_decl("epsilon", data=data, out=out3)

    server = FastMCP("activation-connector")

    @server.tool
    async def active_namespaces() -> list[str]:
        """The namespaces this session has activated."""
        return sorted(await session_active_namespaces())

    registry = attach_upstreams(server, (alpha, beta, epsilon))
    assert registry is not None

    # Nothing is listed, and no process starts, before activation.
    async with Client(server, mode="legacy") as client:
        names = sorted(tool.name for tool in await client.list_tools())
        assert {"alpha_save", "beta_read", "epsilon_save"}.isdisjoint(names)
        active = await client.call_tool("active_namespaces", {})
        assert active.data == []
    assert not startup_marker(out, "tools").exists()

    # Session A activates two of three; the third hits the cap.
    async with Client(server, mode="legacy") as client:
        await client.call_tool("registry_activate", {"namespace": "alpha"})
        await client.call_tool("registry_activate", {"namespace": "beta"})
        names = sorted(tool.name for tool in await client.list_tools())
        assert "alpha_save" in names and "beta_read" in names
        assert "epsilon_save" not in names
        active = await client.call_tool("active_namespaces", {})
        assert active.data == ["alpha", "beta"]

        with pytest.raises(ToolError, match="UPSTREAM_ACTIVE_CAP"):
            await client.call_tool("registry_activate", {"namespace": "epsilon"})
        with pytest.raises(ToolError, match="UPSTREAM_NAMESPACE_UNKNOWN"):
            await client.call_tool("registry_activate", {"namespace": "zzz"})

        await client.call_tool("registry_deactivate", {"namespace": "alpha"})
        names = sorted(tool.name for tool in await client.list_tools())
        assert "alpha_save" not in names and "beta_read" in names
        active = await client.call_tool("active_namespaces", {})
        assert active.data == ["beta"]

    # Session B starts hidden again: activation never leaks across sessions.
    async with Client(server, mode="legacy") as client:
        names = sorted(tool.name for tool in await client.list_tools())
        assert "alpha_save" not in names and "beta_save" not in names
    assert startup_marker(out, "tools").exists()


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914.7")
async def test_gate_hides_a_lazy_namespace_outside_a_session(tmp_path: Path) -> None:
    from fastmcp.server.providers import LocalProvider
    from fastmcp.tools import Tool

    from scion_sdk.mcp.upstreams.gate import ActivationGate
    from scion_sdk.mcp.upstreams.health import UpstreamRegistry

    decl = fixture_decl("alpha", data=tmp_path / "d", out=tmp_path / "o")
    registry = UpstreamRegistry(
        (decl,), activation_setting="lazy", error_strategy="warn"
    )
    inner = LocalProvider()
    inner.add_tool(Tool.from_function(lambda: "x", name="save"))

    # A lazy gate with no session context lists nothing.
    hidden = ActivationGate(
        inner, decl, registry=registry, activation="lazy", pool=None
    )
    assert await hidden.list_tools() == []

    # An active gate always lists.
    active = ActivationGate(
        inner, decl, registry=registry, activation="active", pool=None
    )
    listed = await active.list_tools()
    assert [tool.name for tool in listed] == ["save"]
