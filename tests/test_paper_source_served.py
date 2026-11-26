"""Served paper search and download feed generated ingestion."""

import asyncio
import json
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP
from live_engine import live_ingest, scoped_client  # noqa: F401
from source_graph_helpers import import_source_mapping, sync_failure_cycle
from test_contracts_and_sink import _catalog
from test_event_feeds import CONNECTOR
from test_paper_search_served import _HttpProvider

from scion_sdk.adapters.mcp_tool import McpToolSourceAdapter
from scion_sdk.adapters.paper_download import (
    DocumentAdmission,
    DocumentRequest,
    PaperDocumentService,
)
from scion_sdk.adapters.paper_ingest_tool import register_paper_provider_stream
from scion_sdk.adapters.paper_models import DocumentOffer, PaperQuery
from scion_sdk.adapters.paper_provider import ProviderPage
from scion_sdk.adapters.paper_quota import PaperQuotaPolicy, SharedPaperQuota
from scion_sdk.adapters.paper_search import PaperSearchService, ProviderBinding
from scion_sdk.adapters.paper_tool import (
    register_paper_download_tool,
    register_paper_search_tool,
)
from scion_sdk.http.options import HttpClientOptions
from scion_sdk.http.retry import RetryPolicy
from scion_sdk.manifest.presets import ToolPreset
from scion_sdk.sinks.medulla import MedullaSink
from scion_sdk.testing.http_server import ScriptedHttpServer, ScriptedResponse

_JSON = {"Content-Type": "application/json"}


class _Paged(_HttpProvider):
    async def search(self, query, cursor):
        page = await super().search(query, cursor)
        return ProviderPage(
            records=page.records, cursor="1" if cursor is None else None
        )


def _feed(paper):
    return json.dumps(
        {"id": paper, "title": "Graph " + paper, "doi": "10.1234/" + paper}
    ).encode()


async def _exercise(url, root):
    engine = await scoped_client(["*"])
    try:
        context = await import_source_mapping(
            engine,
            CONNECTOR,
            {
                "Event": {
                    "fields": {"abstract": "xsd:string"},
                    "ontology_class": "Document",
                }
            },
        )

        async def authority(_connector):
            return _catalog(), context

        async def allow(query, ctx):
            return query.tenant == "allowed" and ctx is not None

        quota = SharedPaperQuota(engine)
        policy = PaperQuotaPolicy(
            provider_id="arxiv",
            credential_group="arxiv-served",
            policy_revision="v1",
            minimum_spacing_ns=1,
        )
        search = PaperSearchService(
            {"arxiv": ProviderBinding(_Paged("arxiv", url), policy, retries=0)},
            quota=quota,
            authorize=allow,
        )

        async def admit(request, ctx):
            return DocumentAdmission(
                root=Path(root),
                http=HttpClientOptions(
                    base_url=url,
                    pin_egress=True,
                    allow_loopback=True,
                    retry=RetryPolicy(max_attempts=1),
                ),
                quota=policy,
            )

        server = FastMCP("papers")
        register_paper_search_tool(server, search)
        register_paper_download_tool(server, PaperDocumentService(quota, admit))
        query = PaperQuery(
            tenant="allowed",
            query="graph",
            provider_ids=("arxiv",),
            schema_revision="v1",
            page_bound=1,
        )
        register_paper_provider_stream(server, search, query, name="paper_arxiv_ingest")
        request = DocumentRequest(
            tenant="allowed",
            paper_id="doi:10.1234/served",
            revision=None,
            offer=DocumentOffer(
                provider_id="arxiv", url=url + "/paper", media_type="text/html"
            ),
        )
        async with Client(server) as client:
            found = await client.call_tool(
                "paper_search", {"query": query.model_dump(mode="json")}
            )
            saved = await client.call_tool(
                "paper_download", {"request": request.model_dump(mode="json")}
            )
        preset = ToolPreset(
            name="paper-arxiv",
            server=CONNECTOR,
            tool="paper_arxiv_ingest",
            id_field="id",
            records_path="records",
            params_style="args",
            params={"mode": "delta"},
            pagination="cursor",
            cursor_param="cursor",
            cursor_path="next",
            updated_since_param="state",
            checkpoint_path="checkpoint",
        )

        def adapter():
            return McpToolSourceAdapter(
                preset,
                connector=CONNECTOR,
                mapping_reference=f"connector:{CONNECTOR}#mappings/Event",
            )

        synced = await sync_failure_cycle(
            server,
            MedullaSink(engine, authority),
            CONNECTOR,
            "paper-arxiv",
            "paper_arxiv_ingest",
            adapter,
        )
        rows = await engine.query.cypher_read("MATCH (n) RETURN n LIMIT 100")
        return found.structured_content, saved.structured_content, synced, rows
    finally:
        await engine.close()


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.2")
@pytest.mark.spec("SCION-SOURCE-INGEST-R005.3")
@pytest.mark.spec("SCION-SOURCE-INGEST-R005.6")
def test_served_search_download_and_sync(live_ingest, tmp_path):  # noqa: F811
    page = ScriptedResponse(body=_feed("0001.0001"), headers=_JSON)
    html = b"<html><body><p>Research</p></body></html>"
    with ScriptedHttpServer(
        page,
        ScriptedResponse(body=html, headers={"Content-Type": "text/html"}),
        ScriptedResponse(body=_feed("1234.5678"), headers=_JSON),
        ScriptedResponse(status=503, body=b"error"),
        ScriptedResponse(body=_feed("9876.5432"), headers=_JSON),
    ) as server:
        found, saved, synced, rows = asyncio.run_coroutine_threadsafe(
            _exercise(server.base_url, tmp_path), live_ingest._loop
        ).result(60)
    first, before, failed, second, _ = synced
    assert found["completeness"] == "complete"
    assert saved["reason"] is None
    receipt = saved["receipt"]
    assert (tmp_path / receipt["artifact_ref"]).read_bytes() == html
    assert first.accepted == 1 and second.accepted == 1
    assert before.accepted_checkpoint == failed.accepted_checkpoint
    nodes = [r["n"] for r in rows if r["n"].get("source_stream") == "paper-arxiv"]
    assert len(nodes) == 2, nodes
