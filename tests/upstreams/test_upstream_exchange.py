"""SCION-CONNECTOR-CONTROL-R914.13: a declared upstream runs its calls through the exchange."""

import json
import sys

import pytest
from fastmcp import Client

from scion_sdk.connector import build_server
from scion_sdk.mcp.upstreams import ExchangeBinding, SandboxRoots, UpstreamDecl
from scion_sdk.mcp.upstreams.exchange import bind_exchange_roots, load_exchange_runtime
from tests.exchange.exchange_support import (
    build_served_connector,
    configure,
    route_share_to,
)
from tests.storage.dav_double import DavDouble


async def _echo(tmp_path, monkeypatch, *, bound: bool, arguments: dict) -> dict:
    build_served_connector(tmp_path, monkeypatch, bound=bound)
    try:
        _args, mcp = build_server("democonn", command_args=["--transport", "stdio"])
        async with Client(mcp, mode="legacy") as client:
            await client.call_tool("registry_activate", {"namespace": "alpha"})
            result = await client.call_tool(
                "alpha_echo", arguments, meta={"scion/run": "run-3"}
            )
    finally:
        for name in [m for m in sys.modules if m.split(".")[0] == "democonn"]:
            sys.modules.pop(name)
    return json.loads(result.content[0].text)


@pytest.mark.timeout(300)
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914.13")
async def test_bound_upstream_rewrites_paths_stages_inputs_and_publishes(
    tmp_path, monkeypatch
):
    settings = configure(tmp_path, monkeypatch)
    dav = DavDouble()
    dav.collections.update({"ArtCraft", "ArtCraft/in"})
    dav.files["ArtCraft/in/a.png"] = b"a"
    route_share_to(monkeypatch, dav)
    staging = settings.staging_root

    decl = UpstreamDecl(
        name="alpha",
        command="app-cli",
        sandbox=SandboxRoots(
            read=("/data",), write=("/work",), read_flag="--r", write_flag="--w"
        ),
        exchange=ExchangeBinding(),
    )
    runtime = load_exchange_runtime([decl], "democonn")
    bound = bind_exchange_roots(decl, runtime)
    assert bound.sandbox.read == ("/data", str(staging / "in"))
    assert bound.sandbox.write == ("/work", str(staging / "out"))

    absolute = str(tmp_path / "elsewhere.png")
    echoed = await _echo(
        tmp_path,
        monkeypatch,
        bound=True,
        arguments={
            "path": "sub/out.png",
            "src": "exchange://files/in/a.png",
            "paths": [absolute],
        },
    )
    assert echoed["path"] == str(
        staging / "out" / "alpha" / "run-3" / "sub" / "out.png"
    )
    assert echoed["src"] == str(staging / "in" / "alpha" / "run-3" / "a.png")
    assert echoed["paths"] == [absolute]

    plain = await _echo(
        tmp_path,
        monkeypatch,
        bound=False,
        arguments={"path": "sub/out.png", "src": "exchange://files/in/a.png"},
    )
    assert ExchangeBinding().path_args == ("path", "dir", "out", "out_dir")
    assert plain == {
        "path": "sub/out.png",
        "src": "exchange://files/in/a.png",
        "paths": [],
    }
