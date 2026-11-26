"""A real connector server serves its contract resources and announces a seed change (R068)."""

from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest
from fastmcp import Client

from scion_sdk.mcp.change_events import serve_change_subscriptions
from scion_sdk.mcp.content import ConnectorContent, refresh_seed_resource
from scion_sdk.mcp.server import create_mcp_server
from scion_sdk.ports.change_source import ChangeEvent, ChangeSource
from scion_sdk.ports.session import TransportEndpoint
from scion_sdk.transports.mcp import McpTransport
from tests.test_seed_graph import Widget

SEED_URI = "seed://widget-mcp/seed.ttl"
BOUND_S = 5  # the in-process bus delivers at once; the bound only stops a lost event


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R068")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R068.6")
async def test_connector_serves_contract_resources_and_notifies(tmp_path: Path) -> None:
    ontology = tmp_path / "ontology"
    ontology.mkdir()
    (ontology / "widget.ttl").write_text(
        "@prefix : <https://knuckles-team.github.io/ontology/core#> .\n"
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "<http://example.invalid/widget> a owl:Ontology ; "
        "owl:imports <https://knuckles-team.github.io/ontology/core> .\n"
        ":Widget a owl:Class .\n:serialNumber a owl:DatatypeProperty .\n"
    )
    _, server, _ = create_mcp_server(
        "widget-mcp",
        version="1.0.0",
        command_args=[],
        content=ConnectorContent("widget-mcp", tmp_path, "1.0.0"),
    )
    serve_change_subscriptions(server)
    async with Client(server) as client:
        uris = {str(r.uri) for r in await client.list_resources()}
        assert {
            "connector://resources",
            "ontology://widget-mcp/widget.ttl",
            SEED_URI,
        } <= uris
        manifest = json.loads(
            (await client.read_resource("connector://resources"))[0].text
        )
        assert manifest["descriptors"] == []
        first = {r.uri: r for r in await client.list_resources()}[SEED_URI].meta

    async with McpTransport().session(TransportEndpoint(in_process=server)) as session:
        assert isinstance(session, ChangeSource)
        assert await session.watch([SEED_URI])

        @server.tool(annotations={"readOnlyHint": True})
        def list_widgets(cursor: str | None = None) -> list[Widget]:
            return []

        assert await refresh_seed_resource(server) is True
        seen: set[ChangeEvent] = set()
        with anyio.fail_after(BOUND_S):
            while ChangeEvent("resources") not in seen:
                seen |= await session.next_changes()

    async with Client(server) as client:
        second = {r.uri: r for r in await client.list_resources()}[SEED_URI].meta
        body = (await client.read_resource(SEED_URI))[0].text
    assert first and second
    assert first["scion/digest"] != second["scion/digest"]
    assert "urn:scion:seed:hasResource" in body
