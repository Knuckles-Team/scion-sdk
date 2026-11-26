"""The graph discovery probe bounds and the unapproved-mapping refusal (R909)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import anyio
import pytest

from scion_sdk.transports.graph_probe import (
    MAX_PROBE_ROWS,
    GraphProbe,
    ProbeRefused,
    submit_approved_mapping,
)


@pytest.mark.spec("SCION-SOURCE-INGEST-R909")
async def test_probe_bounds_and_unapproved_mapping_refusal() -> None:
    seen: list[int] = []

    async def reader(query: str, limit: int) -> Sequence[Mapping[str, Any]]:
        seen.append(limit)
        return [{"id": 1, "name": "a"}, {"id": "2", "name": None}]

    probe = GraphProbe("graph", reader)
    proposal = await probe.probe("MATCH (n) RETURN n", max_rows=10)
    assert seen == [10] and proposal.rows_read == 2
    assert proposal.fields["id"] == ("int", "str")
    for query, rows, code in (
        ("MATCH (n) DETACH DELETE n", 5, "PROBE_NOT_READ_ONLY"),
        ("mutation { x }", 5, "PROBE_NOT_READ_ONLY"),
        ("MATCH (n) RETURN n", MAX_PROBE_ROWS + 1, "PROBE_ROWS_OUT_OF_BOUNDS"),
    ):
        with pytest.raises(ProbeRefused) as caught:
            await probe.probe(query, max_rows=rows)
        assert caught.value.code == code
    assert seen == [10]  # a refused probe never reads

    async def slow(query: str, limit: int) -> Sequence[Mapping[str, Any]]:
        await anyio.sleep(5)
        return []

    with pytest.raises(ProbeRefused) as timed:
        await GraphProbe("graph", slow, timeout_seconds=0.01).probe(
            "MATCH (n) RETURN n"
        )
    assert timed.value.code == "PROBE_TIMEOUT"
    sent: list[object] = []

    async def importer(value: object) -> object:
        sent.append(value)
        return "imported"

    with pytest.raises(ProbeRefused) as unapproved:
        await submit_approved_mapping(proposal, owner_approval=None, importer=importer)
    assert unapproved.value.code == "MAPPING_NOT_APPROVED" and sent == []
    assert (
        await submit_approved_mapping(
            proposal, owner_approval="owner:1", importer=importer
        )
        == "imported"
    )
