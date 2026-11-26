"""Raw-corpus search port: exact lexical search, bounded span read, context check.

SCION-SOURCE-INGEST-R903. All three operations share one byte and line
coordinate system and one immutable source snapshot. The provider reports raw
measures (``scan_bytes``, ``scan_lines``); the engine converts them to cost units.
"""

from __future__ import annotations

from typing import Annotated, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "MAX_SPAN_BYTES",
    "ContextCheck",
    "ContextResult",
    "LexicalQuery",
    "OverCeiling",
    "RawHit",
    "RawSourceProvider",
    "RawSourceRef",
    "RawSourceRefusal",
    "SearchResult",
    "SpanRead",
    "SpanResult",
]

MAX_SPAN_BYTES = 65536
_Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_REFUSALS = frozenset(
    {
        "SNAPSHOT_UNKNOWN",
        "DOCUMENT_UNKNOWN",
        "SPAN_INVALID",
        "SPAN_TOO_LARGE",
        "SOURCE_UNAUTHORIZED",
    }
)


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RawSourceRef(_Closed):
    """One immutable snapshot of one source."""

    source_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)


class LexicalQuery(_Closed):
    term: str = Field(min_length=1)
    max_hits: int = Field(ge=1, le=1000)
    max_scan_bytes: int = Field(ge=1)


class _Range(_Closed):
    document_id: str = Field(min_length=1)
    start_byte: int = Field(ge=0)
    end_byte: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end_byte < self.start_byte:
            raise ValueError("end_byte precedes start_byte")
        return self


class RawHit(_Range):
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class SpanRead(_Range):
    max_bytes: int = Field(default=MAX_SPAN_BYTES, ge=1, le=MAX_SPAN_BYTES)


class ContextCheck(_Range):
    expected_sha256: _Sha256


class _Measured(_Closed):
    source_revision: str
    snapshot_id: str
    scan_bytes: int = Field(ge=0)
    scan_lines: int = Field(ge=0)


class OverCeiling(_Closed):
    """The typed abstention: the scan stopped at the caller's ceiling."""

    scan_bytes: int = Field(ge=0)
    next_document_id: str


class SearchResult(_Measured):
    hits: tuple[RawHit, ...] = ()
    over_ceiling: OverCeiling | None = None


class SpanResult(_Measured):
    document_id: str
    start_byte: int
    end_byte: int
    text: str


class ContextResult(_Measured):
    matches: bool


class RawSourceRefusal(RuntimeError):
    """A refusal with a closed ``code``; the message never holds document text."""

    def __init__(self, code: str, *, scan_bytes: int = 0, scan_lines: int = 0) -> None:
        if code not in _REFUSALS:
            raise ValueError(f"unknown raw-source refusal: {code}")
        self.code = code
        self.scan_bytes = scan_bytes
        self.scan_lines = scan_lines
        super().__init__(code)


class RawSourceProvider(Protocol):
    """A source with no search index that still answers all three operations."""

    async def search(self, ref: RawSourceRef, query: LexicalQuery) -> SearchResult: ...

    async def read_span(self, ref: RawSourceRef, span: SpanRead) -> SpanResult: ...

    async def check_context(
        self, ref: RawSourceRef, check: ContextCheck
    ) -> ContextResult: ...
