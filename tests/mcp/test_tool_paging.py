"""A large connector serves tools/list in bounded pages (R915)."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client, FastMCP
from mcp.shared.exceptions import MCPError
from mcp_types import PaginatedRequestParams

from scion_sdk.mcp.server import create_mcp_server
from scion_sdk.mcp.tool_paging import (
    MAX_PAGE_BYTES,
    MAX_PAGE_TOOLS,
    decode_cursor,
    encode_cursor,
    install_tool_paging,
    page_tools,
)


def _server(count: int) -> FastMCP[Any]:
    _a, mcp, _m = create_mcp_server(
        "big", version="0.0.0", command_args=["--transport", "stdio"]
    )
    for n in range(count):

        def make(n: int) -> None:
            @mcp.tool(name=f"op_{n:05d}", description="d" * 1000)
            def op(value: str = "") -> str:
                return value

        make(n)
    return mcp


async def _pages(client: Client[Any]) -> list[Any]:
    pages, cursor = [], None
    while True:
        result = await client.session.list_tools(
            params=PaginatedRequestParams(cursor=cursor)
        )
        pages.append(result)
        cursor = result.next_cursor
        if cursor is None:
            return pages


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R915")
async def test_a_large_catalog_is_served_in_bounded_pages() -> None:
    mcp = _server(3000)
    async with Client(mcp) as client:
        pages = await _pages(client)
        names = [t.name for page in pages for t in page.tools]
        resources = await client.session.list_resources()
    native = {
        t.name for page in pages for t in page.tools if not t.name.startswith("op_")
    }
    assert len(names) == len(set(names)) == 3000 + len(native)
    assert len(pages) > 1 and pages[-1].next_cursor is None
    assert all(p.next_cursor for p in pages[:-1])
    for page in pages:
        assert len(page.tools) <= MAX_PAGE_TOOLS
        encoded = sum(
            len(t.model_dump_json(by_alias=True, exclude_none=True)) for t in page.tools
        )
        assert encoded <= MAX_PAGE_BYTES
    assert resources.next_cursor is None


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R915")
async def test_a_stale_cursor_restarts_the_listing() -> None:
    mcp = _server(1500)
    async with Client(mcp) as client:
        first = await client.session.list_tools()
        assert first.next_cursor
        mcp.tool(name="late_tool")(lambda: "x")
        with pytest.raises(MCPError) as caught:
            await client.session.list_tools(
                params=PaginatedRequestParams(cursor=first.next_cursor)
            )
        assert caught.value.code == -32602


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R915")
def test_cursor_codec_and_pager_units() -> None:
    assert decode_cursor(encode_cursor("rev", 5), "rev") == 5
    with pytest.raises(MCPError):
        decode_cursor(encode_cursor("old", 5), "rev")
    with pytest.raises(MCPError):
        decode_cursor("not-a-cursor", "rev")
    page, nxt = page_tools([], None)
    assert page == [] and nxt is None
    assert callable(install_tool_paging)
