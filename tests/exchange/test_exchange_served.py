"""SCION-CENTRALIZED-CAPABILITIES-R043.7: a served upstream call exchanges its files."""

import sys

import pytest
from fastmcp import Client

from scion_sdk.connector import build_server
from tests.exchange.exchange_support import (
    build_served_connector,
    configure,
    route_share_to,
)
from tests.storage.dav_double import DavDouble


@pytest.mark.timeout(300)
@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R043.7")
async def test_upstream_call_stages_input_publishes_output_and_links(
    tmp_path, monkeypatch
):
    configure(tmp_path, monkeypatch)
    dav = DavDouble()
    dav.collections.update({"ArtCraft", "ArtCraft/in"})
    dav.files["ArtCraft/in/photo.png"] = b"png-bytes"
    route_share_to(monkeypatch, dav)
    build_served_connector(tmp_path, monkeypatch)
    try:
        _args, mcp = build_server("democonn", command_args=["--transport", "stdio"])
        async with Client(mcp, mode="legacy") as client:
            await client.call_tool("registry_activate", {"namespace": "alpha"})
            result = await client.call_tool(
                "alpha_copy",
                {"src": "exchange://files/in/photo.png", "path": "out.png"},
                meta={"scion/run": "run-7"},
            )
    finally:
        for name in [m for m in sys.modules if m.split(".")[0] == "democonn"]:
            sys.modules.pop(name)
    assert result.content[0].text == "copied:out.png"
    links = [block for block in result.content if block.type == "resource_link"]
    assert len(links) == 1 and links[0].uri.startswith("exchange://")
    stored = [
        path for path, data in dav.files.items() if path.endswith("run-7/out.png")
    ]
    assert stored and dav.files[stored[0]] == b"png-bytes"
    disk = list((tmp_path / "disk" / "artifacts" / "alpha").rglob("out.png"))
    assert disk and disk[0].read_bytes() == b"png-bytes"
