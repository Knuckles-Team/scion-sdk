"""SCION-CONNECTOR-CONTROL-R914: the SDK serves a declarative registry behind one connector server."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from connector_dist import build
from fastmcp import Client
from upstream_fixtures import FIXTURE_SCRIPT, startup_marker

from scion_sdk.connector import build_server
from scion_sdk.mcp.upstreams.activation import REGISTRY_TOOL_NAMES
from scion_sdk.upstream_wire import UPSTREAMS_RESOURCE_URI

INIT_TEMPLATE = """\
from democonn.client import Api, get_client

from scion_sdk.connector import Connector
from scion_sdk.mcp.upstreams import SandboxRoots, UpstreamDecl

UPSTREAMS = (
    UpstreamDecl(
        name="alpha",
        command={command!r},
        args=[{script!r}, "--fixture-kind", "tools"],
        sandbox=SandboxRoots(
            read=({data_a!r},),
            write=({out_a!r},),
            read_flag="--fixture-read-root",
            write_flag="--fixture-write-root",
        ),
    ),
    UpstreamDecl(
        name="beta",
        command={command!r},
        args=[{script!r}, "--fixture-kind", "tools"],
        sandbox=SandboxRoots(
            read=({data_b!r},),
            write=({out_b!r},),
            read_flag="--fixture-read-root",
            write_flag="--fixture-write-root",
        ),
    ),
)

connector = Connector(
    api=Api,
    get_client=get_client,
    instructions="Demo connector.",
    upstreams=UPSTREAMS,
)
"""

REGISTRY_TOOLS = frozenset(REGISTRY_TOOL_NAMES)


@pytest.fixture
def upstream_democonn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    out_a = tmp_path / "out-a"
    out_b = tmp_path / "out-b"
    for directory in (data, out_a, out_b):
        directory.mkdir()
    init = INIT_TEMPLATE.format(
        command=sys.executable,
        script=str(FIXTURE_SCRIPT),
        data_a=str(data),
        data_b=str(data),
        out_a=str(out_a),
        out_b=str(out_b),
    )
    build(tmp_path, init=init, sources=None)
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in [
        module for module in sys.modules if module.split(".")[0] == "democonn"
    ]:
        monkeypatch.delitem(sys.modules, name)
    yield tmp_path
    for name in [
        module for module in sys.modules if module.split(".")[0] == "democonn"
    ]:
        sys.modules.pop(name)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914")
async def test_two_stdio_upstreams_are_namespaced_and_lazy(
    upstream_democonn: Path,
) -> None:
    out_a = upstream_democonn / "out-a"
    args, mcp = build_server("democonn", command_args=["--transport", "stdio"])
    assert args.transport == "stdio"

    async with Client(mcp, mode="legacy") as client:
        names = sorted(tool.name for tool in await client.list_tools())
        # The initial list holds the local tools and the four registry tools
        # only: nothing from either upstream is revealed.
        assert any("list_items" in name for name in names)
        assert set(names) >= REGISTRY_TOOLS
        assert "alpha_save" not in names and "beta_save" not in names
        assert not startup_marker(out_a, "tools").exists()

        result = await client.call_tool("registry_activate", {"namespace": "alpha"})
        assert result.data == {"namespace": "alpha", "state": "active", "changed": True}
        names = sorted(tool.name for tool in await client.list_tools())
        # The activated namespace appears namespaced; the other stays hidden.
        assert "alpha_save" in names and "alpha_whoami" in names
        assert "beta_save" not in names and "beta_whoami" not in names

        pid_first = str((await client.call_tool("alpha_whoami", {})).data)
        pid_second = str((await client.call_tool("alpha_whoami", {})).data)
        assert pid_first == pid_second  # one pooled process behind the proxy

        content = await client.read_resource(UPSTREAMS_RESOURCE_URI)
        body = json.loads(content[0].text)
        states = {entry["namespace"]: entry for entry in body["upstreams"]}
        assert states["alpha"]["state"] == "ready"
        assert states["beta"]["state"] == "inactive"

    # The child started exactly when the namespace was first listed.
    assert (
        startup_marker(out_a, "tools").read_text(encoding="utf-8").strip() == pid_first
    )
