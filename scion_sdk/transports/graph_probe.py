"""Bounded read-only discovery probe for external graph and GraphQL sources.

SCION-SOURCE-INGEST-R909. The probe reads at most 10,000 rows within 30 seconds
and proposes a mapping. A proposal reaches the engine's ``ConnectorPack.Import``
only after owner approval; an unapproved proposal is refused before any call.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import anyio

__all__ = [
    "MAX_PROBE_ROWS",
    "GraphProbe",
    "ProbeRefused",
    "submit_approved_mapping",
]

MAX_PROBE_ROWS = 10_000
PROBE_TIMEOUT_SECONDS = 30.0
_WRITES = re.compile(
    r"\b(mutation|create|merge|delete|detach|set|remove|drop|insert|update)\b", re.I
)


class ProbeRefused(RuntimeError):
    """The probe or submission was refused; ``code`` is closed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class MappingProposal:
    """Fields observed in the sampled rows, each with its observed value kinds."""

    source: str
    fields: Mapping[str, tuple[str, ...]]
    rows_read: int


class GraphProbe:
    """Runs one bounded read through ``reader(query, limit)``."""

    def __init__(
        self,
        source: str,
        reader: Callable[[str, int], Awaitable[Sequence[Mapping[str, Any]]]],
        *,
        timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
    ) -> None:
        if not 0 < timeout_seconds <= PROBE_TIMEOUT_SECONDS:
            raise ValueError("probe timeout is outside 0 to 30 seconds")
        self._source = source
        self._reader = reader
        self._timeout = timeout_seconds

    async def probe(
        self, query: str, *, max_rows: int = MAX_PROBE_ROWS
    ) -> MappingProposal:
        if not 1 <= max_rows <= MAX_PROBE_ROWS:
            raise ProbeRefused("PROBE_ROWS_OUT_OF_BOUNDS")
        if _WRITES.search(query):
            raise ProbeRefused("PROBE_NOT_READ_ONLY")
        try:
            with anyio.fail_after(self._timeout):
                rows = await self._reader(query, max_rows)
        except TimeoutError:
            raise ProbeRefused("PROBE_TIMEOUT") from None
        if len(rows) > max_rows:
            raise ProbeRefused("PROBE_ROWS_EXCEEDED")
        kinds: dict[str, set[str]] = {}
        for row in rows:
            for name, value in row.items():
                kinds.setdefault(name, set()).add(type(value).__name__)
        return MappingProposal(
            self._source,
            {name: tuple(sorted(found)) for name, found in sorted(kinds.items())},
            len(rows),
        )


async def submit_approved_mapping(
    proposal: MappingProposal,
    *,
    owner_approval: str | None,
    importer: Callable[[MappingProposal], Awaitable[object]],
) -> object:
    """Send ``proposal`` to ``ConnectorPack.Import`` only with an owner approval."""
    if not owner_approval:
        raise ProbeRefused("MAPPING_NOT_APPROVED")
    return await importer(proposal)
