"""Connector papers with citation edges reach the real engine (R005.33, R005.36)."""

import asyncio

import pytest
from fastmcp import FastMCP
from live_engine import live_ingest, scoped_client  # noqa: F401
from source_graph_helpers import import_source_mapping
from test_contracts_and_sink import _catalog
from test_paper_contract import _paper, _query

from scion_sdk.adapters.mcp_tool import McpToolSourceAdapter
from scion_sdk.adapters.paper_facts import paper_fact_records
from scion_sdk.adapters.paper_merge import extract_paper_facts
from scion_sdk.adapters.paper_models import CitationEdge, FieldEvidence
from scion_sdk.adapters.paper_provider import ProviderPaper
from scion_sdk.manifest.presets import ToolPreset
from scion_sdk.ports.session import TransportEndpoint
from scion_sdk.runner.syncing import SyncTarget, sync_stream
from scion_sdk.sinks.medulla import MedullaSink
from scion_sdk.transports.mcp import McpTransport

CONNECTOR = "citation-demo"
FIELDS = {"label": "xsd:string", "title": "xsd:string", "canonical_id": "xsd:string"}


def _papers():
    cited = FieldEvidence(provider_id="first", source_id="a", location="references")
    cites = _paper("first", "a", title="Cites", doi="10.1234/a").model_copy(
        update={
            "citation_edges": (
                CitationEdge(
                    cited_canonical_id="doi:10.1234/b", source_evidence=(cited,)
                ),
            )
        }
    )
    cited_paper = _paper("first", "b", title="Cited", doi="10.1234/b")

    def evidence(record):
        return {
            "title": (
                FieldEvidence(provider_id="first", source_id=record, location="title"),
            )
        }

    orphan = ProviderPaper(
        reference=cites.reference.model_copy(update={"record_id": "c"}),
        title="Orphan",
        unidentified=True,
        field_provenance=evidence("c"),
    )
    broken = ProviderPaper(
        reference=cites.reference.model_copy(update={"record_id": "d"}),
        title="Broken",
        doi="not-a-doi",
        field_provenance=evidence("d"),
    )
    return cites, cited_paper, orphan, broken


async def _exercise():
    outcome = extract_paper_facts(_query(page_bound=10), _papers())
    server = FastMCP("citations")

    @server.tool()
    def paper_facts() -> dict[str, object]:
        return {"records": paper_fact_records(outcome)}

    engine = await scoped_client(["*"])
    try:
        context = await import_source_mapping(
            engine,
            CONNECTOR,
            {"Paper": {"fields": FIELDS, "ontology_class": "Document"}},
        )

        async def authority(_connector):
            return _catalog(), context

        preset = ToolPreset(
            name="paper_facts",
            server=CONNECTOR,
            tool="paper_facts",
            id_field="id",
            records_path="records",
            params_style="args",
            params={},
            pagination="none",
        )
        adapter = McpToolSourceAdapter(
            preset,
            connector=CONNECTOR,
            mapping_reference=f"connector:{CONNECTOR}#mappings/Paper",
        )
        target = SyncTarget(CONNECTOR, MedullaSink(engine, authority), max_pages=1)
        async with McpTransport().session(TransportEndpoint(in_process=server)) as s:
            summary = await sync_stream(s, adapter, target)
        rows = await engine.query.cypher_read("MATCH (n) RETURN n LIMIT 100")
        return outcome, summary, rows
    finally:
        await engine.close()


@pytest.fixture
def served(live_ingest):  # noqa: F811
    return asyncio.run_coroutine_threadsafe(_exercise(), live_ingest._loop).result(60)


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.33")
def test_connector_papers_reach_source_ingest(served):
    outcome, summary, rows = served
    nodes = [n["n"] for n in rows if n["n"].get("source_stream") == "paper_facts"]
    assert summary.accepted == len(nodes) == len(paper_fact_records(outcome))
    assert any(n.get("canonical_id", "").startswith("unidentified:") for n in nodes)


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.36")
def test_paper_facts_and_edges(served):
    outcome, _summary, rows = served
    nodes = [n["n"] for n in rows if n["n"].get("source_stream") == "paper_facts"]
    labels = sorted(n["label"] for n in nodes)
    assert labels.count("Citation") == 1 and labels.count("Paper") == 3
    # the broken paper is refused alone, leaves no row and does not affect the rest
    assert [r.record_id for r in outcome.rejections] == ["d"]
    assert "Broken" not in {n.get("title") for n in nodes}
    assert {n.get("title") for n in nodes if n["label"] == "Paper"} == {
        "Cites",
        "Cited",
        "Orphan",
    }
