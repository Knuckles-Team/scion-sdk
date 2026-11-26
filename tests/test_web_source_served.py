"""Two web-source connectors sync one page each into the real engine (R005.40.6)."""

import asyncio

import pytest
from fastmcp import FastMCP
from live_engine import live_ingest, scoped_client  # noqa: F401
from source_graph_helpers import import_source_mapping
from test_contracts_and_sink import _catalog
from test_web_source_contract import FixtureSource

from scion_sdk.adapters.mcp_tool import McpToolSourceAdapter
from scion_sdk.manifest.presets import ToolPreset
from scion_sdk.ports.session import TransportEndpoint
from scion_sdk.ports.web_source import WebFetchRequest, register_web_source_tools
from scion_sdk.runner.syncing import SyncTarget, sync_stream
from scion_sdk.sinks.medulla import MedullaSink
from scion_sdk.testing.web_source_conformance import fixture_site
from scion_sdk.transports.mcp import McpTransport

BACKENDS = ("webarchiver", "archivebox-api")
CONNECTOR = "web-demo"
FIELDS = {
    "content_digest": "xsd:string",
    "title": "xsd:string",
    "backend": "xsd:string",
    "label": "xsd:string",
}


def _connector(backend: str, url: str) -> FastMCP:
    """A fixture connector: the contract tools plus the ingest tool the sync reads."""
    source = FixtureSource(backend=backend)
    server = FastMCP(backend)
    register_web_source_tools(server, source)

    @server.tool()
    async def page_ingest() -> dict[str, object]:
        page = await source.extract(WebFetchRequest(url=url))
        record = {
            "id": page.canonical_url,
            "label": str(page.status),
            "content_digest": page.content_digest,
            "title": page.title,
            "backend": page.backend,
        }
        return {"records": [record]}

    return server


async def _sync_backend(engine, context, backend: str, url: str):
    async def authority(_connector):
        return _catalog(), context

    preset = ToolPreset(
        name=f"page_{backend}",
        server=CONNECTOR,
        tool="page_ingest",
        id_field="id",
        records_path="records",
        params_style="args",
        params={},
        pagination="none",
    )
    adapter = McpToolSourceAdapter(
        preset,
        connector=CONNECTOR,
        mapping_reference=f"connector:{CONNECTOR}#mappings/Page",
    )
    target = SyncTarget(CONNECTOR, MedullaSink(engine, authority), max_pages=1)
    async with McpTransport().session(
        TransportEndpoint(in_process=_connector(backend, url))
    ) as session:
        return await sync_stream(session, adapter, target)


async def _exercise(url: str):
    engine = await scoped_client(["*"])
    try:
        context = await import_source_mapping(
            engine,
            CONNECTOR,
            {"Page": {"fields": FIELDS, "ontology_class": "Document"}},
        )
        summaries = [await _sync_backend(engine, context, b, url) for b in BACKENDS]
        rows = await engine.query.cypher_read("MATCH (n) RETURN n LIMIT 100")
        return summaries, rows
    finally:
        await engine.close()


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.40.6")
def test_two_backends_produce_equal_records(live_ingest):  # noqa: F811
    with fixture_site() as site:
        summaries, rows = asyncio.run_coroutine_threadsafe(
            _exercise(f"{site.base_url}/article"), live_ingest._loop
        ).result(60)
    assert [s.accepted for s in summaries] == [1, 1]
    nodes = [
        n["n"] for n in rows if n["n"].get("source_stream", "").startswith("page_")
    ]
    assert len(nodes) == 2, nodes
    comparable = {
        tuple(sorted((k, v) for k, v in n.items() if k in FIELDS and k != "backend"))
        for n in nodes
    }
    assert len(comparable) == 1  # equal records except the backend field
    assert {n["backend"] for n in nodes} == set(BACKENDS)
