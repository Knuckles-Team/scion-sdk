"""The workspace reader feeds connector-sync with on-disk files and the real engine (R005.21, R005.24)."""

import asyncio
import hashlib
import io
from pathlib import Path

import pytest
from fastmcp import FastMCP
from live_engine import live_ingest, scoped_client  # noqa: F401
from pypdf import PdfWriter
from source_graph_helpers import import_source_mapping
from test_contracts_and_sink import _catalog
from test_workspace_reader import _entry, _request, _zip

from scion_sdk.adapters.mcp_tool import McpToolSourceAdapter
from scion_sdk.adapters.workspace_models import WorkspaceFile
from scion_sdk.adapters.workspace_reader import WorkspaceReader
from scion_sdk.manifest.presets import ToolPreset
from scion_sdk.ports.session import TransportEndpoint
from scion_sdk.runner.syncing import SyncTarget, sync_stream
from scion_sdk.sinks.medulla import MedullaSink
from scion_sdk.transports.mcp import McpTransport

CONNECTOR = "workspace-demo"
WORD = _zip(
    {
        "[Content_Types].xml": "<Types/>",
        "word/document.xml": '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p></w:body></w:document>',
    }
)


def _pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _files(root: Path) -> tuple[WorkspaceFile, ...]:
    good = (
        _entry(root, "a.txt", "text", b"one\ntwo\n"),
        _entry(
            root, "b.vtt", "vtt", b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n"
        ),
        _entry(root, "c.docx", "docx", WORD),
        _entry(root, "e.pdf", "pdf", _pdf()),
    )
    # a file whose bytes differ from the manifest digest: a visible per-file failure
    bad = _entry(root, "f.txt", "text", b"changed")
    (root / "f.txt").write_bytes(b"changed after the manifest was cut")
    return (*good, bad)


def _server(root: Path, files, monkeypatch) -> FastMCP:
    reader = WorkspaceReader(root, authority=str(root))

    async def in_thread(file, req):
        # The parser worker processes cannot start from the engine loop thread; the
        # same parser runs in a thread here, and the engine socket stays usable.
        return await asyncio.to_thread(reader._read_one, file, req)

    monkeypatch.setattr(reader, "_run_process", in_thread)
    request = _request(files).model_copy(update={"root_authority": str(root)})
    server = FastMCP("workspace")

    @server.tool()
    async def read_workspace() -> dict[str, object]:
        outcomes = await reader.read(request)
        return {
            "records": [
                {
                    "id": o.file.relative_path,
                    "label": o.outcome,
                    "format": o.file.format,
                    "reason": o.reason,
                    "segments": len(o.locations),
                }
                for o in outcomes
            ]
        }

    return server


async def _exercise(server):
    engine = await scoped_client(["*"])
    try:
        context = await import_source_mapping(
            engine,
            CONNECTOR,
            {"File": {"fields": {"label": "xsd:string"}, "ontology_class": "Document"}},
        )

        async def authority(_connector):
            return _catalog(), context

        preset = ToolPreset(
            name="read_workspace",
            server=CONNECTOR,
            tool="read_workspace",
            id_field="id",
            records_path="records",
            params_style="args",
            params={},
            pagination="none",
        )
        adapter = McpToolSourceAdapter(
            preset,
            connector=CONNECTOR,
            mapping_reference=f"connector:{CONNECTOR}#mappings/File",
        )
        target = SyncTarget(CONNECTOR, MedullaSink(engine, authority), max_pages=1)
        async with McpTransport().session(TransportEndpoint(in_process=server)) as s:
            summary = await sync_stream(s, adapter, target)
        rows = await engine.query.cypher_read("MATCH (n) RETURN n LIMIT 100")
        return summary, rows
    finally:
        await engine.close()


def _sync(live_ingest, tmp_path, monkeypatch):  # noqa: F811
    files = _files(tmp_path)
    before = {f.relative_path: (tmp_path / f.relative_path).read_bytes() for f in files}
    server = _server(tmp_path, files, monkeypatch)
    summary, rows = asyncio.run_coroutine_threadsafe(
        _exercise(server), live_ingest._loop
    ).result(60)
    after = {f.relative_path: (tmp_path / f.relative_path).read_bytes() for f in files}
    nodes = [n["n"] for n in rows if n["n"].get("source_stream") == "read_workspace"]
    return files, summary, nodes, before, after


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.21")
def test_registered_reader_syncs(live_ingest, tmp_path, monkeypatch):  # noqa: F811
    files, summary, nodes, _, _ = _sync(live_ingest, tmp_path, monkeypatch)
    assert summary.accepted == len(files) == len(nodes)


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.24")
def test_parallel_formats_and_refusals(live_ingest, tmp_path, monkeypatch):  # noqa: F811
    files, _summary, nodes, before, after = _sync(live_ingest, tmp_path, monkeypatch)
    assert before == after  # inputs are never modified
    assert hashlib.sha256(after["a.txt"]).hexdigest() == files[0].sha256
    by_label = sorted(n.get("label") for n in nodes)
    assert (
        by_label == ["failed"] + ["ok"] * 4
    )  # one success per format, one visible failure
