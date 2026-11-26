"""The read-time access-check tool over a real MCP client (SCION-SOURCE-INGEST-R805.5)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fastmcp import Client, FastMCP

from scion_sdk.manifest.sources import IngestSourceSpec
from scion_sdk.mcp.access_check_tool import (
    ACCESS_CHECK_PROVIDER_MISSING,
    ACCESS_MODE_UNDECLARED,
    AccessCheckToolRefused,
    register_access_check_tool,
)
from scion_sdk.ports.access_check import AccessCheckAnswer


def _source(mode: str | None) -> IngestSourceSpec:
    return IngestSourceSpec.model_validate(
        {
            "source_id": "docs",
            "access_mode": mode,
            "record_type": "Doc",
            "tools": ["list_docs"],
            "id_field": "id",
        }
    )


class _Provider:
    def __init__(self, hang: bool = False) -> None:
        self.hang = hang

    async def check(
        self, principal: str, record_refs: tuple[str, ...]
    ) -> AccessCheckAnswer:
        if self.hang:
            await asyncio.sleep(30)
        allowed = tuple(r for r in record_refs if r != "secret")
        denied = tuple(r for r in record_refs if r == "secret")
        return AccessCheckAnswer(allowed, denied, datetime.now(UTC))


@pytest.mark.spec("SCION-SOURCE-INGEST-R805.5")
async def test_tool_answers_over_mcp_and_fails_closed() -> None:
    server = FastMCP("access")
    register_access_check_tool(server, _Provider(), [_source("checked_at_read")])
    async with Client(server) as client:
        result = await client.call_tool(
            "source_access_check", {"principal": "u1", "record_refs": ["a", "secret"]}
        )
    assert result.structured_content["allowed"] == ["a"]
    assert result.structured_content["denied"] == ["secret"]
    with pytest.raises(AccessCheckToolRefused) as undeclared:
        register_access_check_tool(FastMCP("x"), _Provider(), [_source("synced")])
    assert undeclared.value.code == ACCESS_MODE_UNDECLARED
    with pytest.raises(AccessCheckToolRefused) as missing:
        register_access_check_tool(FastMCP("x"), None, [_source("checked_at_read")])
    assert missing.value.code == ACCESS_CHECK_PROVIDER_MISSING
