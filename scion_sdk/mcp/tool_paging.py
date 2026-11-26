"""Serve ``tools/list`` in bounded pages.

SCION-CONNECTOR-CONTROL-R915. A connector with one tool per upstream operation
lists thousands of tools; one response of that size exceeds the event limit a
hosting client reads. This helper replaces the server's ``tools/list`` handler
with one that pages after visibility filtering. A page holds at most
:data:`MAX_PAGE_TOOLS` tools and :data:`MAX_PAGE_BYTES` of encoded descriptors,
whichever comes first; one oversized descriptor is a page of its own. The cursor
is opaque and bound to one catalog revision; a cursor of an older revision
answers invalid params so the client restarts the listing. ``resources/list``
and ``prompts/list`` stay single-page.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import mcp_types
from fastmcp.server.dependencies import bind_request_context
from fastmcp.utilities.versions import dedupe_with_versions
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError

__all__ = [
    "MAX_PAGE_BYTES",
    "MAX_PAGE_TOOLS",
    "decode_cursor",
    "encode_cursor",
    "install_tool_paging",
    "page_tools",
]

MAX_PAGE_TOOLS = 1_000
MAX_PAGE_BYTES = 1_048_576
INVALID_PARAMS = -32602


def _revision(sizes: list[tuple[str, int]]) -> str:
    body = json.dumps(sizes, separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()[:16]


def encode_cursor(revision: str, index: int) -> str:
    raw = json.dumps({"r": revision, "i": index}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str, revision: str) -> int:
    """The start index of ``cursor``; invalid or stale cursors raise invalid params."""
    try:
        body = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if body["r"] != revision or not isinstance(body["i"], int) or body["i"] < 0:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise MCPError(INVALID_PARAMS, "invalid or stale tools/list cursor") from None
    return int(body["i"])


def page_tools(tools: list[Any], cursor: str | None) -> tuple[list[Any], str | None]:
    """One page of ``tools`` from ``cursor`` and the next cursor, or ``None``."""
    sizes = [
        (t.name, len(t.model_dump_json(by_alias=True, exclude_none=True)))
        for t in tools
    ]
    revision = _revision(sizes)
    start = decode_cursor(cursor, revision) if cursor else 0
    if start > len(tools):
        raise MCPError(INVALID_PARAMS, "invalid or stale tools/list cursor")
    end, used = start, 0
    while end < len(tools) and end - start < MAX_PAGE_TOOLS:
        size = sizes[end][1]
        if end > start and used + size > MAX_PAGE_BYTES:
            break
        used += size
        end += 1
    next_cursor = encode_cursor(revision, end) if end < len(tools) else None
    return tools[start:end], next_cursor


def install_tool_paging(mcp: Any) -> None:
    """Replace the ``tools/list`` handler of ``mcp`` with the bounded pager."""

    async def on_list_tools(
        ctx: ServerRequestContext, params: mcp_types.PaginatedRequestParams | None
    ) -> mcp_types.ListToolsResult:
        with bind_request_context(ctx):
            tools = dedupe_with_versions(list(await mcp.list_tools()), lambda t: t.name)
            sdk_tools = [tool.to_mcp_tool(name=tool.name) for tool in tools]
            page, next_cursor = page_tools(sdk_tools, params.cursor if params else None)
            return mcp_types.ListToolsResult(tools=page, next_cursor=next_cursor)

    mcp._mcp_server.add_request_handler(
        "tools/list", mcp_types.PaginatedRequestParams, on_list_tools
    )
