"""An offline bookmark export reaches ingestion through discovery and sync (R005.9)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

from scion_sdk.adapters.bookmark_models import BookmarkRequest
from scion_sdk.adapters.bookmark_source import BookmarkReader
from scion_sdk.adapters.mcp_tool import McpToolSourceAdapter
from scion_sdk.manifest.model import SchemaMapping
from scion_sdk.manifest.presets import ToolPreset
from scion_sdk.ports.session import TransportEndpoint
from scion_sdk.runner.syncing import SyncTarget, sync_stream
from scion_sdk.testing.sinks import InMemorySink
from scion_sdk.transports.mcp import McpTransport

CONNECTOR = "bookmark-fixture"


def _server(root: Path) -> FastMCP:
    reader = BookmarkReader(root, vocabulary_revision="v1", domains={})
    server = FastMCP(CONNECTOR)

    @server.tool(name="read_bookmarks", annotations={"readOnlyHint": True})
    async def read_bookmarks(offset: int = 0) -> dict[str, Any]:
        first = await reader.read(
            BookmarkRequest(
                relative_path="bookmarks.json",
                export_revision="social-v1",
                vocabulary_revision="v1",
            )
        )
        result = (
            first
            if offset == 0
            else await reader.read(
                BookmarkRequest(
                    relative_path="bookmarks.json",
                    export_revision="social-v1",
                    vocabulary_revision="v1",
                    offset=offset,
                    file_sha256=first.file_sha256,
                    vocabulary_sha256=first.vocabulary_sha256,
                )
            )
        )
        document = result.model_dump(mode="json")
        document["next_cursor"] = (
            None if result.next_offset is None else str(result.next_offset)
        )
        return document

    return server


def _adapter() -> McpToolSourceAdapter:
    preset = ToolPreset(
        name="bookmarks",
        server=CONNECTOR,
        tool="read_bookmarks",
        id_field="bookmark_id",
        records_path="bookmarks",
        params_style="args",
        pagination="cursor",
        cursor_param="offset",
        cursor_path="next_cursor",
    )
    return McpToolSourceAdapter(
        preset,
        connector=CONNECTOR,
        mapping_reference=f"connector:{CONNECTOR}#mappings/Bookmark",
        schema_mappings={"Bookmark": SchemaMapping(ontology_class="Document")},
    )


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.9")
@pytest.mark.spec("SCION-SOURCE-INGEST-R005.12")
async def test_registered_reader_syncs_through_discovery(tmp_path: Path) -> None:
    rows = [{"id": f"post-{n}", "text": f"offline {n}"} for n in range(3)]
    (tmp_path / "bookmarks.json").write_text(json.dumps({"bookmarks": rows}))
    sink = InMemorySink()
    target = SyncTarget(CONNECTOR, sink, max_pages=5)
    adapter = _adapter()
    async with McpTransport().session(
        TransportEndpoint(in_process=_server(tmp_path))
    ) as session:
        descriptor = await adapter.discover(session)
        outcome = await sync_stream(session, adapter, target)
    assert descriptor.tool == "read_bookmarks"
    assert outcome.records == 3 and outcome.accepted == 3 and outcome.exhausted
    ids = {r.record_id for b in sink.batches.values() for r in b.records}
    assert len(ids) == 3
