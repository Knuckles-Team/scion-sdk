"""Search, span, check and ceiling over MCP tools/call on an unindexed corpus (R903.6)."""

from __future__ import annotations

import hashlib

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from scion_sdk.ports.raw_source import RawSourceRef
from scion_sdk.raw_source import RawCorpusProvider, register_raw_source_tools

REF = {"source_id": "docs", "snapshot_id": "s1"}
DOCS = {"a.txt": b"alpha needle\nbeta\n", "b.txt": b"gamma needle\n"}


@pytest.mark.spec("SCION-SOURCE-INGEST-R903")
@pytest.mark.spec("SCION-SOURCE-INGEST-R903.6")
async def test_unindexed_corpus_search_span_check_and_ceiling() -> None:
    provider = RawCorpusProvider(
        RawSourceRef(**REF), source_revision="r9", documents=lambda: DOCS.items()
    )
    server = FastMCP("raw")
    register_raw_source_tools(server, provider)
    total = sum(len(v) for v in DOCS.values())
    async with Client(server) as client:
        found = (
            await client.call_tool(
                "raw_search",
                {
                    "ref": REF,
                    "query": {"term": "needle", "max_hits": 5, "max_scan_bytes": total},
                },
            )
        ).structured_content
        assert [h["document_id"] for h in found["hits"]] == ["a.txt", "b.txt"]
        assert found["scan_bytes"] == total and found["source_revision"] == "r9"
        hit = found["hits"][0]
        span = (
            await client.call_tool(
                "raw_read_span",
                {
                    "ref": REF,
                    "span": {
                        k: hit[k] for k in ("document_id", "start_byte", "end_byte")
                    },
                },
            )
        ).structured_content
        assert span["text"] == "needle"
        check = (
            await client.call_tool(
                "raw_check_context",
                {
                    "ref": REF,
                    "check": {
                        "document_id": "a.txt",
                        "start_byte": hit["start_byte"],
                        "end_byte": hit["end_byte"],
                        "expected_sha256": hashlib.sha256(b"needle").hexdigest(),
                    },
                },
            )
        ).structured_content
        assert check["matches"] is True
        capped = (
            await client.call_tool(
                "raw_search",
                {
                    "ref": REF,
                    "query": {"term": "needle", "max_hits": 5, "max_scan_bytes": 5},
                },
            )
        ).structured_content
        assert capped["over_ceiling"]["next_document_id"] == "a.txt"
        assert capped["scan_bytes"] == 0
        with pytest.raises(ToolError, match="SNAPSHOT_UNKNOWN"):
            await client.call_tool(
                "raw_search",
                {
                    "ref": {**REF, "snapshot_id": "old"},
                    "query": {"term": "x", "max_hits": 1, "max_scan_bytes": 9},
                },
            )
