"""SCION-CONNECTOR-CONTROL-R914.5: the loader turns declarations into namespaced providers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP
from fastmcp.tools import Tool
from upstream_fixtures import clean_upstream_env, fixture_decl, startup_marker

from scion_sdk.mcp.upstreams import UpstreamDeclError
from scion_sdk.mcp.upstreams.loader import attach_upstreams
from scion_sdk.mcp.upstreams.tagging import (
    UPSTREAM_META_KEY,
    UPSTREAM_META_NAMESPACE_KEY,
    NamespaceTag,
    upstream_metadata,
)
from scion_sdk.upstream_wire import UPSTREAMS_RESOURCE_URI


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914.5")
async def test_namespaced_names_tags_and_collision_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_upstream_env(monkeypatch)
    monkeypatch.setenv("SCION_UPSTREAMS_LIST_CACHE_TTL_S", "0")
    data = tmp_path / "data"
    out_a = tmp_path / "out-a"
    out_b = tmp_path / "out-b"
    for directory in (data, out_a, out_b):
        directory.mkdir()
    alpha = fixture_decl("alpha", data=data, out=out_a)
    beta = fixture_decl("beta", data=data, out=out_b)

    server = FastMCP("wiring-connector")
    registry = attach_upstreams(server, (alpha, beta))
    assert registry is not None

    async with Client(server, mode="legacy") as client:
        await client.call_tool("registry_activate", {"namespace": "alpha"})
        await client.call_tool("registry_activate", {"namespace": "beta"})
        names = sorted(tool.name for tool in await client.list_tools())
        resource = await client.read_resource(UPSTREAMS_RESOURCE_URI)

    # Both fixture upstreams expose `save`; the namespace prefix splits them.
    assert "alpha_save" in names and "alpha_whoami" in names
    assert "beta_save" in names and "beta_compute" in names
    assert names.count("save") == 0  # no un-namespaced tool survives
    assert startup_marker(out_a, "tools").read_text(encoding="utf-8").strip().isdigit()
    body = json.loads(resource[0].text)
    assert {entry["namespace"] for entry in body["upstreams"]} == {"alpha", "beta"}

    # A local tool inside a namespace prefix refuses at startup.
    clash = FastMCP("clash-connector")

    @clash.tool(name="alpha_save")
    def alpha_save(what: str) -> str:
        """A local tool that steals the namespace."""
        return what

    with pytest.raises(UpstreamDeclError) as excinfo:
        attach_upstreams(clash, (alpha,))
    assert excinfo.value.code == "UPSTREAM_TOOL_COLLISION"

    # The registry's own tool names are reserved.
    reserved = FastMCP("reserved-connector")

    @reserved.tool(name="registry_activate")
    def registry_activate() -> str:
        """A local tool with a reserved name."""
        return "no"

    with pytest.raises(UpstreamDeclError) as excinfo:
        attach_upstreams(reserved, (alpha,))
    assert excinfo.value.code == "UPSTREAM_TOOL_COLLISION"


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914.5")
async def test_namespace_transform_stamps_tag_and_metadata(tmp_path: Path) -> None:
    decl = fixture_decl("alpha", data=tmp_path / "d", out=tmp_path / "o")
    transform = NamespaceTag(decl)
    tool = Tool.from_function(lambda: "x", name="save")
    stamped = await transform.list_tools([tool])
    assert "ns:alpha" in stamped[0].tags
    meta = upstream_metadata(decl)
    assert stamped[0].meta == meta
    assert meta[UPSTREAM_META_KEY][UPSTREAM_META_NAMESPACE_KEY] == "alpha"
    assert meta[UPSTREAM_META_KEY]["upstream"] == "alpha"


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914.5")
async def test_registry_tools_are_attached_under_reserved_names(tmp_path: Path) -> None:
    from scion_sdk.mcp.upstreams.activation import (
        REGISTRY_TOOL_NAMES,
        attach_registry_tools,
    )
    from scion_sdk.mcp.upstreams.health import UpstreamRegistry

    decl = fixture_decl("alpha", data=tmp_path / "d", out=tmp_path / "o")
    registry = UpstreamRegistry(
        (decl,), activation_setting="lazy", error_strategy="warn"
    )
    server = FastMCP("registry-tools-connector")
    attach_registry_tools(server, registry, max_active_namespaces=4)

    async with Client(server, mode="legacy") as client:
        names = {tool.name for tool in await client.list_tools()}
    assert set(REGISTRY_TOOL_NAMES) <= names
    assert len(REGISTRY_TOOL_NAMES) == 4
