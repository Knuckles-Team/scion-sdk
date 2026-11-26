"""Conformance suite every raw-source provider passes (SCION-SOURCE-INGEST-R903.3)."""

from __future__ import annotations

from dataclasses import dataclass

from scion_sdk.ports.raw_source import (
    LexicalQuery,
    RawSourceProvider,
    RawSourceRef,
)

__all__ = ["RawSourceSuiteFailure", "RawSuiteFixture", "raw_source_suite"]


class RawSourceSuiteFailure(AssertionError):
    """A provider does not meet the raw-corpus contract."""


@dataclass(frozen=True)
class RawSuiteFixture:
    ref: RawSourceRef
    term: str
    corpus_bytes: int


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RawSourceSuiteFailure(message)


async def raw_source_suite(
    provider: RawSourceProvider, fixture: RawSuiteFixture
) -> None:
    """An indexed-only provider scans nothing and ignores the ceiling; it fails."""
    full = await provider.search(
        fixture.ref,
        LexicalQuery(
            term=fixture.term, max_hits=10, max_scan_bytes=fixture.corpus_bytes
        ),
    )
    _require(bool(full.hits), "the term has no exact hit")
    _require(full.scan_bytes > 0, "no scan measure: the provider is indexed-only")
    capped = await provider.search(
        fixture.ref, LexicalQuery(term=fixture.term, max_hits=10, max_scan_bytes=1)
    )
    _require(capped.over_ceiling is not None, "the scan ceiling is not honored")
    _require(capped.scan_bytes <= 1, "the provider read past the ceiling")
