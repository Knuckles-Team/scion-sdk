"""The meeting tools register on a served connector and dispatch over MCP."""

from __future__ import annotations

import pytest
from fastmcp import Client, FastMCP
from test_meeting_client import D1, _Engine, pinned_serves_the_method, seg  # noqa: F401

from scion_sdk.meeting import MeetingTranscriptClient
from scion_sdk.meeting.tools import MEETING_TOOLS, register_meeting_tools


@pytest.mark.spec("SCION-SOURCE-INGEST-R014.3")
async def test_tools_registered_and_dispatched_over_mcp(pinned_serves_the_method):  # noqa: F811
    server = FastMCP("meeting")

    @server.tool(name="dictate")
    def dictate(audio_ref: str) -> str:
        return "dictation:" + audio_ref

    engine = _Engine()
    register_meeting_tools(
        server,
        MeetingTranscriptClient(engine, clock_ms=lambda: 7, provenance_ref="p"),
    )
    async with Client(server) as client:
        names = {tool.name for tool in await client.list_tools()}
        assert set(MEETING_TOOLS) <= names and "dictate" in names
        first = seg(0)
        appended = await client.call_tool(
            "meeting_append", {"segments": [first.model_dump(mode="json")]}
        )
        assert appended.structured_content == {"accepted": 1}
        sealed = await client.call_tool(
            "meeting_finalize",
            {"meeting_id": "m", "ordered_segment_ids": ["s0"], "final_revision": 1},
        )
        assert sealed.structured_content["finalization_digest"] == D1
        read = await client.call_tool(
            "meeting_get_final",
            {"meeting_id": "m", "ordered_segment_ids": ["s0"], "final_revision": 1},
        )
        assert read.structured_content == sealed.structured_content
        assert (await client.call_tool("dictate", {"audio_ref": "a"})).data == (
            "dictation:a"
        )
