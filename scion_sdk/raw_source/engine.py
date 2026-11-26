"""Exact search, span read and context check over a document iterator.

SCION-SOURCE-INGEST-R903.2 and R903.4. Documents are scanned in id order,
case-sensitively. The scan stops before a document that would pass the ceiling.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable

from scion_sdk.ports.raw_source import (
    ContextCheck,
    ContextResult,
    LexicalQuery,
    OverCeiling,
    RawHit,
    RawSourceRef,
    RawSourceRefusal,
    SearchResult,
    SpanRead,
    SpanResult,
)

__all__ = ["RawCorpusProvider", "check_context", "exact_search", "read_span"]


def exact_search(
    documents: Iterable[tuple[str, bytes]],
    query: LexicalQuery,
    *,
    source_revision: str,
    snapshot_id: str,
) -> SearchResult:
    """Scan ``documents`` (already in id order) for ``query.term``."""
    term = query.term.encode("utf-8")
    hits: list[RawHit] = []
    scan_bytes = scan_lines = 0
    for document_id, data in documents:
        if scan_bytes + len(data) > query.max_scan_bytes:
            return SearchResult(
                source_revision=source_revision,
                snapshot_id=snapshot_id,
                scan_bytes=scan_bytes,
                scan_lines=scan_lines,
                over_ceiling=OverCeiling(
                    scan_bytes=scan_bytes, next_document_id=document_id
                ),
            )
        scan_bytes += len(data)
        scan_lines += data.count(b"\n") + (
            1 if data and not data.endswith(b"\n") else 0
        )
        position = data.find(term)
        while position != -1 and len(hits) < query.max_hits:
            end = position + len(term)
            hits.append(
                RawHit(
                    document_id=document_id,
                    start_byte=position,
                    end_byte=end,
                    start_line=data.count(b"\n", 0, position) + 1,
                    end_line=data.count(b"\n", 0, end) + 1,
                )
            )
            position = data.find(term, end)
        if len(hits) >= query.max_hits:
            break
    return SearchResult(
        hits=tuple(hits),
        source_revision=source_revision,
        snapshot_id=snapshot_id,
        scan_bytes=scan_bytes,
        scan_lines=scan_lines,
    )


def _range(data: bytes, start: int, end: int) -> bytes:
    if end > len(data):
        raise RawSourceRefusal("SPAN_INVALID")
    piece = data[start:end]
    try:
        piece.decode("utf-8")
    except UnicodeDecodeError:
        raise RawSourceRefusal("SPAN_INVALID") from None
    for edge in (start, end):
        if edge < len(data) and (data[edge] & 0xC0) == 0x80:
            raise RawSourceRefusal("SPAN_INVALID")
    return piece


def read_span(
    data: bytes, span: SpanRead, *, source_revision: str, snapshot_id: str
) -> SpanResult:
    """Return the exact bytes of one range; refuse one that splits a character."""
    if span.end_byte - span.start_byte > span.max_bytes:
        raise RawSourceRefusal("SPAN_TOO_LARGE")
    piece = _range(data, span.start_byte, span.end_byte)
    return SpanResult(
        document_id=span.document_id,
        start_byte=span.start_byte,
        end_byte=span.end_byte,
        text=piece.decode("utf-8"),
        source_revision=source_revision,
        snapshot_id=snapshot_id,
        scan_bytes=len(piece),
        scan_lines=piece.count(b"\n") + (1 if piece else 0),
    )


def check_context(
    data: bytes, check: ContextCheck, *, source_revision: str, snapshot_id: str
) -> ContextResult:
    """Whether the SHA-256 of the range equals ``expected_sha256``."""
    piece = _range(data, check.start_byte, check.end_byte)
    return ContextResult(
        matches=hashlib.sha256(piece).hexdigest() == check.expected_sha256,
        source_revision=source_revision,
        snapshot_id=snapshot_id,
        scan_bytes=len(piece),
        scan_lines=piece.count(b"\n") + (1 if piece else 0),
    )


class RawCorpusProvider:
    """A provider over an unindexed corpus: ``documents`` yields (id, bytes)."""

    def __init__(
        self,
        ref: RawSourceRef,
        *,
        source_revision: str,
        documents: Callable[[], Iterable[tuple[str, bytes]]],
        authorize: Callable[[], bool] = lambda: True,
    ) -> None:
        self._ref = ref
        self._revision = source_revision
        self._documents = documents
        self._authorize = authorize

    def _admit(self, ref: RawSourceRef) -> None:
        # Authorize first, then bind the snapshot; nothing is read before either.
        if not self._authorize():
            raise RawSourceRefusal("SOURCE_UNAUTHORIZED")
        if ref != self._ref:
            raise RawSourceRefusal("SNAPSHOT_UNKNOWN")

    def _document(self, document_id: str) -> bytes:
        for candidate, data in self._documents():
            if candidate == document_id:
                return data
        raise RawSourceRefusal("DOCUMENT_UNKNOWN")

    async def search(self, ref: RawSourceRef, query: LexicalQuery) -> SearchResult:
        self._admit(ref)
        return exact_search(
            sorted(self._documents()),
            query,
            source_revision=self._revision,
            snapshot_id=self._ref.snapshot_id,
        )

    async def read_span(self, ref: RawSourceRef, span: SpanRead) -> SpanResult:
        self._admit(ref)
        if span.end_byte - span.start_byte > span.max_bytes:
            raise RawSourceRefusal("SPAN_TOO_LARGE")
        return read_span(
            self._document(span.document_id),
            span,
            source_revision=self._revision,
            snapshot_id=self._ref.snapshot_id,
        )

    async def check_context(
        self, ref: RawSourceRef, check: ContextCheck
    ) -> ContextResult:
        self._admit(ref)
        return check_context(
            self._document(check.document_id),
            check,
            source_revision=self._revision,
            snapshot_id=self._ref.snapshot_id,
        )
