import asyncio
import json

import pytest
from fastmcp import Client, FastMCP
from live_engine import PRINCIPAL, TENANT, live_ingest, scoped_client  # noqa: F401
from source_graph_helpers import import_source_mapping
from test_contracts_and_sink import _catalog
from test_event_feeds import CONNECTOR
from test_paper_search_served import _HttpProvider

from scion_sdk.adapters.mcp_tool import McpToolSourceAdapter
from scion_sdk.adapters.paper_ingest_tool import (
    register_paper_provider_stream,
)
from scion_sdk.adapters.paper_models import PaperQuery
from scion_sdk.adapters.paper_provider import ProviderPage
from scion_sdk.adapters.paper_quota import PaperQuotaPolicy, SharedPaperQuota
from scion_sdk.adapters.paper_search import (
    PaperSearchService,
    ProviderBinding,
)
from scion_sdk.adapters.paper_tool import register_paper_maintenance_tool
from scion_sdk.manifest.presets import ToolPreset
from scion_sdk.sinks.medulla import MedullaSink
from scion_sdk.testing.http_server import ScriptedHttpServer, ScriptedResponse


async def _synced(url):
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

        async def authorize(query, ctx):
            return query.tenant == "allowed" and ctx is not None

        policy = PaperQuotaPolicy(
            provider_id="arxiv",
            credential_group="arxiv-graph",
            policy_revision="v1",
            minimum_spacing_ns=1,
        )

        class Provider(_HttpProvider):
            async def search(self, query, cursor):
                page = await super().search(query, cursor)
                return ProviderPage(
                    records=page.records, cursor="1" if cursor is None else None
                )

        service = PaperSearchService(
            {"arxiv": ProviderBinding(Provider("arxiv", url), policy, retries=0)},
            quota=SharedPaperQuota(engine),
            authorize=authorize,
        )
        query = PaperQuery(
            tenant="allowed",
            query="graph",
            provider_ids=("arxiv",),
            schema_revision="v1",
            page_bound=1,
        )
        server = FastMCP("paper-graph")
        register_paper_provider_stream(
            server, service, query, name="paper_arxiv_ingest"
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

        sink = MedullaSink(engine, authority)
        from scion_sdk.ports.session import TransportEndpoint
        from scion_sdk.runner.syncing import SyncTarget, sync_stream
        from scion_sdk.transports.mcp import McpTransport

        target = SyncTarget(CONNECTOR, sink, max_pages=1)
        async with McpTransport().session(
            TransportEndpoint(in_process=server)
        ) as session:
            await sync_stream(session, adapter(), target)
            await sync_stream(session, adapter(), target)
        status = await sink.source_status(CONNECTOR, "paper-arxiv")
        before = await engine.query.cypher_read("MATCH (n) RETURN n LIMIT 100")
        ids = [r["n"] for r in before if r["n"].get("source_stream") == "paper-arxiv"]
        return engine, sink, status, ids, before
    except BaseException:
        await engine.close()
        raise


SR = "https://knuckles-team.github.io/ontology/core#sourceRecordId"


async def _live(engine):
    rows = await engine.query.cypher_read("MATCH (n) RETURN n LIMIT 100")
    return sorted(
        r["n"][SR]
        for r in rows
        if r["n"].get("source_stream") == "paper-arxiv"
        and not any("tombston" in key.lower() for key in r["n"])
    )


async def _drive(url):
    engine, sink, status, nodes, _ = await _synced(url)
    try:
        ids = sorted(n[SR] for n in nodes)
        server = FastMCP("maintenance")
        register_paper_maintenance_tool(
            server, sink, connector=CONNECTOR, stream="paper-arxiv", clock_ms=lambda: 10
        )

        def order(**over):
            base = {
                "order_id": "o1",
                "tenant_id": "t",
                "goal_id": "g",
                "candidate_ids": [ids[0]],
                "expected_revision": status.accepted_checkpoint_digest,
                "cost_ceiling": 1,
                "owner_authorization_ref": "auth:1",
                "admission_verdict_digest": "d" * 64,
                "issued_at_ms": 1,
                "expires_at_ms": 1000,
            }
            base.update(over)
            return base

        out = {}
        async with Client(server) as client:
            for name, change in {
                "stale": {"expected_revision": "e" * 64},
                "expired": {"expires_at_ms": 5},
                "blank": {"owner_authorization_ref": " "},
            }.items():
                result = await client.call_tool(
                    "paper_maintenance_apply", {"order": order(**change)}
                )
                out[name] = result.structured_content
            unchanged = await _live(engine)
            applied = await client.call_tool(
                "paper_maintenance_apply", {"order": order()}
            )
        out["applied"] = applied.structured_content
        out["unchanged"] = unchanged
        out["after"] = await _live(engine)
        out["ids"] = ids
        return out
    finally:
        await engine.close()


@pytest.mark.spec("SCION-SOURCE-INGEST-R015.6")
def test_order_withdraws_candidates_and_refuses_unsafe_orders(live_ingest):  # noqa: F811
    def feed(paper):
        return json.dumps(
            {"id": paper, "title": "Graph " + paper, "doi": "10.1234/" + paper}
        ).encode()

    json_type = {"Content-Type": "application/json"}
    with ScriptedHttpServer(
        ScriptedResponse(body=feed("1234.5678"), headers=json_type),
        ScriptedResponse(body=feed("9876.5432"), headers=json_type),
    ) as server:
        out = asyncio.run_coroutine_threadsafe(
            _drive(server.base_url), live_ingest._loop
        ).result(60)
    assert [out[k]["reason"] for k in ("stale", "expired", "blank")] == [
        "STALE_REVISION",
        "ORDER_EXPIRED",
        "AUTHORIZATION_MISSING",
    ]
    assert out["unchanged"] == out["ids"]
    receipt = out["applied"]["receipt"]
    assert receipt["withdrawn_ids"] == [out["ids"][0]]
    assert len(receipt["engine_receipt_digest"]) == 64
    assert out["after"] == [out["ids"][1]]
