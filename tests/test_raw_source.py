"""Raw-corpus models and behavior on an unindexed fixture (R903.1 to R903.5)."""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from scion_sdk.ports.raw_source import (
    MAX_SPAN_BYTES,
    ContextCheck,
    LexicalQuery,
    OverCeiling,
    RawHit,
    RawSourceProvider,
    RawSourceRef,
    RawSourceRefusal,
    SpanRead,
)
from scion_sdk.raw_source import RawCorpusProvider, exact_search

REF = RawSourceRef(source_id="docs", snapshot_id="s1")
DOCS = {"a.txt": b"alpha\nbeta needle\n", "b.txt": "gamma\nné needle\n".encode()}


def _provider(authorize: bool = True) -> RawCorpusProvider:
    return RawCorpusProvider(
        REF,
        source_revision="r7",
        documents=lambda: DOCS.items(),
        authorize=lambda: authorize,
    )


def _size() -> int:
    return sum(len(v) for v in DOCS.values())


@pytest.mark.spec("SCION-SOURCE-INGEST-R903.1")
def test_models_round_trip_and_reject_invalid_offsets() -> None:
    hit = RawHit(document_id="a", start_byte=1, end_byte=2, start_line=1, end_line=1)
    assert RawHit.model_validate(hit.model_dump()) == hit
    with pytest.raises(ValidationError):
        RawHit(document_id="a", start_byte=5, end_byte=2, start_line=1, end_line=1)
    with pytest.raises(ValidationError):
        SpanRead(document_id="a", start_byte=0, end_byte=1, max_bytes=65537)
    with pytest.raises(ValidationError):
        LexicalQuery(term="", max_hits=1, max_scan_bytes=1)


@pytest.mark.spec("SCION-SOURCE-INGEST-R903.2")
async def test_search_span_check_and_ceiling_on_an_unindexed_fixture() -> None:
    provider = _provider()
    found = await provider.search(
        REF, LexicalQuery(term="needle", max_hits=10, max_scan_bytes=_size())
    )
    assert [(h.document_id, h.start_byte, h.start_line) for h in found.hits] == [
        ("a.txt", 11, 2),
        ("b.txt", 10, 2),
    ]
    assert found.scan_bytes == _size() and found.scan_lines == 4
    span = await provider.read_span(
        REF, SpanRead(document_id="a.txt", start_byte=11, end_byte=17)
    )
    assert span.text == "needle"
    digest = hashlib.sha256(b"needle").hexdigest()
    checked = await provider.check_context(
        REF,
        ContextCheck(
            document_id="a.txt", start_byte=11, end_byte=17, expected_sha256=digest
        ),
    )
    assert checked.matches is True
    first = len(DOCS["a.txt"])
    capped = await provider.search(
        REF, LexicalQuery(term="needle", max_hits=10, max_scan_bytes=first)
    )
    assert capped.hits == () and capped.over_ceiling is not None
    assert capped.over_ceiling.next_document_id == "b.txt"
    assert capped.scan_bytes == first


@pytest.mark.spec("SCION-SOURCE-INGEST-R903.4")
async def test_each_refusal_reads_nothing_past_the_limit() -> None:
    provider = _provider()
    big = SpanRead(document_id="a.txt", start_byte=0, end_byte=9, max_bytes=4)
    refusals = {
        "SPAN_TOO_LARGE": provider.read_span(REF, big),
        "DOCUMENT_UNKNOWN": provider.read_span(
            REF, SpanRead(document_id="zz", start_byte=0, end_byte=1)
        ),
        "SPAN_INVALID": provider.read_span(
            REF, SpanRead(document_id="b.txt", start_byte=8, end_byte=9)
        ),
        "SNAPSHOT_UNKNOWN": provider.read_span(
            RawSourceRef(source_id="docs", snapshot_id="old"),
            SpanRead(document_id="a.txt", start_byte=0, end_byte=1),
        ),
        "SOURCE_UNAUTHORIZED": _provider(False).search(
            REF, LexicalQuery(term="x", max_hits=1, max_scan_bytes=9)
        ),
    }
    for code, call in refusals.items():
        with pytest.raises(RawSourceRefusal) as caught:
            await call
        assert caught.value.code == code and caught.value.scan_bytes == 0
    with pytest.raises(ValueError):
        RawSourceRefusal("INVENTED")


@pytest.mark.spec("SCION-SOURCE-INGEST-R903.5")
async def test_responses_carry_revision_and_measured_scan() -> None:
    result = await _provider().search(
        REF, LexicalQuery(term="needle", max_hits=1, max_scan_bytes=_size())
    )
    assert (result.source_revision, result.snapshot_id) == ("r7", "s1")
    assert result.scan_bytes == len(DOCS["a.txt"])
    assert "needle" not in repr(result.over_ceiling)


@pytest.mark.spec("SCION-SOURCE-INGEST-R903.2")
def test_exact_search_helper_stops_before_the_document_that_passes_the_ceiling() -> (
    None
):
    query = LexicalQuery(term="needle", max_hits=5, max_scan_bytes=len(DOCS["a.txt"]))
    result = exact_search(
        sorted(DOCS.items()), query, source_revision="r", snapshot_id="s"
    )
    assert result.over_ceiling == OverCeiling(
        scan_bytes=len(DOCS["a.txt"]), next_document_id="b.txt"
    )
    provider: RawSourceProvider = _provider()
    assert provider is not None and MAX_SPAN_BYTES == 65536
