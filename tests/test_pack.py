"""A captured tool entry carries its declared readable form (R048.3)."""

from __future__ import annotations

import json

import pytest
from fastmcp import FastMCP

from scion_sdk.artifacts.tools import ToolArtifactKind
from scion_sdk.ports.session import TransportEndpoint
from scion_sdk.transports.mcp import McpTransport


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R048.3")
async def test_tool_entry_carries_readable_form() -> None:
    server = FastMCP("demo")

    @server.tool(
        meta={
            "eg.annotations": {"readable_form": {"template_id": "t1", "max_chars": 120}}
        }
    )
    def analysis() -> dict:
        return {}

    @server.tool()
    def plain() -> dict:
        return {}

    async with McpTransport().session(TransportEndpoint(in_process=server)) as session:
        identity = await session.server_identity()
        entries = await ToolArtifactKind().list_entries(session, identity)
    bodies = {entry.name: json.loads(entry.body) for entry in entries}
    assert bodies["analysis"]["readable_form"] == {
        "template_id": "t1",
        "max_chars": 120,
        "question_set_ref": None,
    }
    assert "readable_form" not in bodies["plain"]


def test_declared_form_is_not_an_engine_annotation() -> None:
    from scion_sdk.artifacts.annotations import READABLE_FORM_KEY

    assert READABLE_FORM_KEY == "readable_form"
