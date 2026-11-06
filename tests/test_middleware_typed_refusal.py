"""A typed refusal passes through the standard error handling (SCION-CONNECTOR-CONTROL-R025.8)."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from scion_sdk.mcp.middleware import (
    TypedRefusalErrorHandlingMiddleware,
    build_middleware,
)


class CodedRefusal(Exception):
    """A connector refusal that names its own code and reason."""

    code = -32010
    reason = "quota_exhausted"


class Denied(PermissionError):
    """A permission refusal subclass (not the bare PermissionError type)."""


def _server() -> FastMCP[Any]:
    mcp: FastMCP[Any] = FastMCP("refusals")

    @mcp.tool
    def tool_refusal() -> str:
        raise ToolError("not_permitted: the table is read only")

    @mcp.tool
    def coded() -> str:
        raise CodedRefusal("daily quota spent")

    @mcp.tool
    def denied() -> str:
        raise Denied("no grant for incident.write")

    @mcp.tool
    def crash() -> str:
        raise RuntimeError("boom")

    for middleware in build_middleware():
        mcp.add_middleware(middleware)
    return mcp


def test_the_standard_stack_uses_the_typed_refusal_handler() -> None:
    assert isinstance(build_middleware()[0], TypedRefusalErrorHandlingMiddleware)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R025.8")
async def test_a_tool_refusal_keeps_its_message() -> None:
    async with Client(_server()) as client:
        result = await client.call_tool("tool_refusal", {}, raise_on_error=False)
    assert result.is_error
    text = result.content[0].text  # type: ignore[union-attr]
    assert "not_permitted: the table is read only" in text
    assert "Internal error" not in text


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R025.8")
async def test_a_coded_refusal_keeps_its_code_and_reason() -> None:
    async with Client(_server()) as client:
        with pytest.raises(MCPError) as caught:
            await client.call_tool("coded", {})
    assert caught.value.code == -32010
    assert caught.value.message == "quota_exhausted: daily quota spent"
    assert caught.value.data == {"reason": "quota_exhausted"}


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R025.8")
async def test_a_permission_subclass_is_a_denial_not_an_internal_error() -> None:
    async with Client(_server()) as client:
        with pytest.raises(MCPError) as caught:
            await client.call_tool("denied", {})
    assert caught.value.code == -32000
    assert "no grant for incident.write" in caught.value.message
    assert "Internal error" not in caught.value.message


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R025.8")
async def test_an_unexpected_exception_is_still_an_internal_error() -> None:
    async with Client(_server()) as client:
        with pytest.raises(MCPError) as caught:
            await client.call_tool("crash", {})
    assert caught.value.code == -32603
    assert caught.value.message.startswith("Internal error")
