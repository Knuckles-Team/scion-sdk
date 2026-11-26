"""Declared transports certify and pack through the public SDK entry points (R069)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp.resources import TextResource
from pydantic import AnyUrl

from scion_sdk.access_contract import parse_access_contracts
from scion_sdk.artifacts.pack import build_content_pack
from scion_sdk.artifacts.resources import ResourceArtifactKind
from scion_sdk.ports.session import TransportEndpoint
from scion_sdk.transports.mcp import McpTransport
from tests.test_transport_contract import _certify, _descriptor, _served_server

CLASS_IRI = "https://example.invalid/demo#Thing"
ONTOLOGY = (
    f"@prefix owl: <http://www.w3.org/2002/07/owl#> .\n<{CLASS_IRI}> a owl:Class .\n"
)


def _add_text(server: Any, uri: str, text: str, mime_type: str) -> None:
    server.add_resource(
        TextResource(uri=AnyUrl(uri), name=uri, text=text, mime_type=mime_type)
    )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R069")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R069.6")
async def test_declared_transports_certify_and_pack() -> None:
    descriptors = []
    for name, kind in (
        ("alpha", "http_endpoint"),
        ("beta", "graphql_query"),
        ("gamma", "mcp_tool"),
    ):
        descriptor = _descriptor(name, kind) | {
            "ontology_class": CLASS_IRI,
            "key_field": "id",
        }
        descriptors.append(descriptor)
    server = _served_server(descriptors)
    _add_text(server, "ontology://demo-mcp/demo.ttl", ONTOLOGY, "text/turtle")
    for descriptor in descriptors:
        _add_text(
            server, str(descriptor["uri"]), json.dumps(descriptor), "application/json"
        )

    report = await _certify(server)
    assert report.passed, report.transport_defects
    assert [row["protocol"] for row in report.document()["transports"]] == [
        "graphql_query",
        "http_endpoint",
        "mcp_tool",
    ]

    async with McpTransport().session(TransportEndpoint(in_process=server)) as session:
        pack = await build_content_pack(
            session, connector="demo-mcp", kinds=(ResourceArtifactKind(),)
        )
    entry = next(e for e in pack.archive.entries if e.uri == "contract://demo-mcp")
    body = pack.archive.data[entry.body.offset : entry.body.offset + entry.body.length]
    contracts = parse_access_contracts(body.decode("utf-8"))
    assert {c.access_kind for c in contracts} == {
        "http_endpoint",
        "graphql_query",
        "mcp_tool",
    }
