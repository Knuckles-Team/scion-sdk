"""The maintenance tool is destructive-hinted and idle until called (SC-R015.3)."""

from __future__ import annotations

import asyncio

import pytest
from fastmcp import Client, FastMCP
from test_paper_maintenance import REVISION, RecordingSink, _order

from scion_sdk.adapters.paper_tool import register_paper_maintenance_tool


@pytest.mark.spec("SCION-SOURCE-INGEST-R015.3")
def test_tool_registered_with_destructive_hint_and_no_scheduler():
    sink = RecordingSink()
    server = FastMCP("papers")
    register_paper_maintenance_tool(
        server, sink, connector="scholarx", stream="papers", clock_ms=lambda: 10
    )

    async def exercise():
        async with Client(server) as client:
            tools = await client.list_tools()
            idle = list(sink.batches)
            applied = await client.call_tool(
                "paper_maintenance_apply",
                {"order": _order().model_dump(mode="json")},
            )
            stale = await client.call_tool(
                "paper_maintenance_apply",
                {"order": _order(expected_revision="f" * 64).model_dump(mode="json")},
            )
        return tools, idle, applied.structured_content, stale.structured_content

    tools, idle, applied, stale = asyncio.run(exercise())
    (tool,) = tools
    assert tool.name == "paper_maintenance_apply"
    assert tool.annotations.destructive_hint is True
    assert idle == []
    assert applied["receipt"]["withdrawn_ids"] == ["doi:1", "doi:2"]
    assert stale == {"receipt": None, "reason": "STALE_REVISION"}
    assert len(sink.batches) == 1
    assert REVISION != "f" * 64
