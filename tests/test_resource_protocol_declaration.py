"""A tool declares a resource protocol and its reference parameter (R911)."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client, FastMCP

from scion_sdk.mcp.tool_declaration import (
    RESOURCE_PARAM_META,
    RESOURCE_PROTOCOL_META,
    RESOURCE_PROTOCOLS,
    ToolDeclarationInvalid,
    resource_declaration,
)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R911")
async def test_declaration_pairs_and_manifest_round_trip() -> None:
    mcp: FastMCP[Any] = FastMCP("tunnel")

    @mcp.tool(meta={RESOURCE_PROTOCOL_META: "ssh", RESOURCE_PARAM_META: "alias"})
    def run(alias: str) -> str:
        return alias

    async with Client(mcp) as client:
        (tool,) = await client.list_tools()
    assert resource_declaration(tool.meta) == ("ssh", "alias")
    assert resource_declaration({}) is None
    assert {"web", "ssh", "rdp", "database"} == RESOURCE_PROTOCOLS
    for bad in (
        {RESOURCE_PROTOCOL_META: "ssh"},
        {RESOURCE_PARAM_META: "alias"},
        {RESOURCE_PROTOCOL_META: "ftp", RESOURCE_PARAM_META: "alias"},
    ):
        with pytest.raises(ToolDeclarationInvalid) as caught:
            resource_declaration(bad)
        assert caught.value.code == "TOOL_DECLARATION_INVALID"
