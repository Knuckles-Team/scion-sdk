"""The raw-source tools over a real MCP client and the conformance suite (R903.3)."""

from __future__ import annotations

import pytest
from fastmcp import Client, FastMCP

from scion_sdk.ports.raw_source import (
    ContextResult,
    LexicalQuery,
    RawSourceRef,
    SearchResult,
    SpanResult,
)
from scion_sdk.raw_source import RawCorpusProvider, register_raw_source_tools
from scion_sdk.testing.raw_source import (
    RawSourceSuiteFailure,
    RawSuiteFixture,
    raw_source_suite,
)

REF = RawSourceRef(source_id="docs", snapshot_id="s1")
DOCS = {"a.txt": b"one needle two\n"}


class _IndexedOnly:
    """Answers from an index: no scan, ignores the ceiling."""

    async def search(self, ref: RawSourceRef, query: LexicalQuery) -> SearchResult:
        return SearchResult.model_validate(
            {
                "hits": [
                    {
                        "document_id": "a.txt",
                        "start_byte": 4,
                        "end_byte": 10,
                        "start_line": 1,
                        "end_line": 1,
                    }
                ],
                "source_revision": "r",
                "snapshot_id": "s1",
                "scan_bytes": 0,
                "scan_lines": 0,
            }
        )

    async def read_span(self, ref: RawSourceRef, span: object) -> SpanResult:
        raise NotImplementedError

    async def check_context(self, ref: RawSourceRef, check: object) -> ContextResult:
        raise NotImplementedError


@pytest.mark.spec("SCION-SOURCE-INGEST-R903.3")
async def test_tools_registered_read_only_and_suite_rejects_an_indexed_only_fixture() -> (
    None
):
    provider = RawCorpusProvider(
        REF, source_revision="r", documents=lambda: DOCS.items()
    )
    server = FastMCP("raw")
    names = register_raw_source_tools(server, provider)
    async with Client(server) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
    assert (
        set(names) == set(tools) == {"raw_search", "raw_read_span", "raw_check_context"}
    )
    assert all(t.annotations and t.annotations.read_only_hint for t in tools.values())
    fixture = RawSuiteFixture(ref=REF, term="needle", corpus_bytes=15)
    await raw_source_suite(provider, fixture)
    with pytest.raises(RawSourceSuiteFailure):
        await raw_source_suite(_IndexedOnly(), fixture)  # type: ignore[arg-type]
