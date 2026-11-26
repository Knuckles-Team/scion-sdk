"""The three read-only raw-source tools (SCION-SOURCE-INGEST-R903.3)."""

from __future__ import annotations

from typing import Any

from scion_sdk.ports.raw_source import (
    ContextCheck,
    ContextResult,
    LexicalQuery,
    RawSourceProvider,
    RawSourceRef,
    SearchResult,
    SpanRead,
    SpanResult,
)

__all__ = ["register_raw_source_tools"]


def register_raw_source_tools(server: Any, provider: RawSourceProvider) -> list[str]:
    """Register ``raw_search``, ``raw_read_span`` and ``raw_check_context``."""

    async def raw_search(ref: RawSourceRef, query: LexicalQuery) -> SearchResult:
        """Exact lexical search over an unindexed snapshot, bounded by a scan ceiling."""
        return await provider.search(ref, query)

    async def raw_read_span(ref: RawSourceRef, span: SpanRead) -> SpanResult:
        """Read the exact bytes of one bounded range."""
        return await provider.read_span(ref, span)

    async def raw_check_context(
        ref: RawSourceRef, check: ContextCheck
    ) -> ContextResult:
        """Whether a range still hashes to the expected digest."""
        return await provider.check_context(ref, check)

    names: list[str] = []
    for function in (raw_search, raw_read_span, raw_check_context):
        server.tool(name=function.__name__, annotations={"readOnlyHint": True})(
            function
        )
        names.append(function.__name__)
    return names
