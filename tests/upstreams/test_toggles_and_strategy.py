"""SCION-CONNECTOR-CONTROL-R914.6: per-upstream toggles and the provider error strategy."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from upstream_fixtures import clean_upstream_env, fixture_decl, startup_marker

from scion_sdk.mcp.upstreams import UpstreamDeclError
from scion_sdk.mcp.upstreams.loader import (
    REGISTRY_SETTING_PREFIX,
    RegistrySettings,
    attach_upstreams,
    read_registry_settings,
)
from scion_sdk.mcp.upstreams.overlay import merge_overlay


async def _two_upstream_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, strategy: str | None = None
) -> tuple[FastMCP, object]:
    clean_upstream_env(monkeypatch)
    monkeypatch.setenv("SCION_UPSTREAMS_LIST_CACHE_TTL_S", "0")
    if strategy is not None:
        monkeypatch.setenv("SCION_UPSTREAM_ERROR_STRATEGY", strategy)
    data = tmp_path / "data"
    out = tmp_path / "out"
    bad = tmp_path / "bad"
    for directory in (data, out, bad):
        directory.mkdir()
    healthy = fixture_decl("alpha", data=data, out=out)
    flaky = fixture_decl("beta", data=data, out=bad, kind="flaky")
    server = FastMCP(f"strategy-{strategy or 'warn'}-connector")
    registry = attach_upstreams(server, (healthy, flaky))
    return server, registry


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914.6")
async def test_disabled_skips_and_warn_isolates_a_failing_upstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_upstream_env(monkeypatch)
    monkeypatch.setenv("SCION_UPSTREAMS_LIST_CACHE_TTL_S", "0")
    data, out, bad, disabled_out = (
        tmp_path / "data",
        tmp_path / "out",
        tmp_path / "bad",
        tmp_path / "disabled",
    )
    for directory in (data, out, bad, disabled_out):
        directory.mkdir()
    healthy = fixture_decl("alpha", data=data, out=out)
    flaky = fixture_decl("beta", data=data, out=bad, kind="flaky")
    disabled = fixture_decl("gamma", data=data, out=disabled_out, enabled=False)

    server = FastMCP("toggles-connector")
    registry = attach_upstreams(server, (healthy, flaky, disabled))
    assert registry is not None

    async with Client(server, mode="legacy") as client:
        await client.call_tool("registry_activate", {"namespace": "alpha"})
        await client.call_tool("registry_activate", {"namespace": "beta"})
        with pytest.raises(ToolError, match="UPSTREAM_DISABLED"):
            await client.call_tool("registry_activate", {"namespace": "gamma"})
        names = sorted(tool.name for tool in await client.list_tools())

    assert "alpha_save" in names
    assert "beta_save" not in names  # the failing upstream lists nothing
    assert startup_marker(out, "tools").exists()
    assert registry.status("beta").state == "unavailable"
    assert registry.status("beta").last_error_code is not None
    assert not startup_marker(disabled_out, "tools").exists()  # disabled spawns nothing
    states = {status.namespace: status.state for status in registry.statuses()}
    assert states["gamma"] == "disabled"


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914.6")
async def test_raise_strategy_fails_the_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, registry = await _two_upstream_server(
        tmp_path, monkeypatch, strategy="raise"
    )

    async with Client(server, mode="legacy") as client:
        # Activating the failing upstream runs its list: under raise the list
        # failure fails the operation that carries it.
        with pytest.raises(MCPError):
            await client.call_tool("registry_activate", {"namespace": "beta"})
        # And an explicit list fails while it still lists that namespace.
        with pytest.raises(MCPError):
            await client.list_tools()

    assert registry.status("beta").state == "unavailable"


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914.6")
def test_bad_overlay_entry_leaves_connector_declarations_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_upstream_env(monkeypatch)
    overlay = tmp_path / "overlay.toml"
    overlay.write_text(
        '[[upstream]]\nname = "alpha"\nkind = "http"\n', encoding="utf-8"
    )
    monkeypatch.setenv(f"{REGISTRY_SETTING_PREFIX}_FILE", str(overlay))
    data, out = tmp_path / "data", tmp_path / "out"
    data.mkdir()
    out.mkdir()
    base = fixture_decl("alpha", data=data, out=out)

    with pytest.raises(UpstreamDeclError) as excinfo:
        merge_overlay((base,), overlay_file=overlay, disabled=())
    assert excinfo.value.code == "UPSTREAM_DECL_INVALID"

    server = FastMCP("overlay-connector")
    registry = attach_upstreams(server, (base,))
    assert registry is not None
    assert [status.namespace for status in registry.statuses()] == ["alpha"]
    assert registry.status("alpha").state == "inactive"

    # The disabled list wins over the overlay.
    merged = merge_overlay((base,), overlay_file=None, disabled=("alpha",))
    assert merged[0].enabled is False


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914.6")
def test_unknown_strategy_refuses_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    clean_upstream_env(monkeypatch)
    knobs = read_registry_settings()
    assert isinstance(knobs, RegistrySettings)
    assert knobs.activation == "lazy" and knobs.error_strategy == "warn"
    assert REGISTRY_SETTING_PREFIX == "SCION_UPSTREAMS"

    monkeypatch.setenv("SCION_UPSTREAM_ERROR_STRATEGY", "yell")
    with pytest.raises(UpstreamDeclError) as excinfo:
        read_registry_settings()
    assert excinfo.value.code == "UPSTREAM_STRATEGY_INVALID"

    monkeypatch.setenv("SCION_UPSTREAM_ERROR_STRATEGY", "raise")
    assert read_registry_settings().error_strategy == "raise"
