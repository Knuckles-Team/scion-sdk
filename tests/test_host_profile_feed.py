"""SCION-OBSERVABILITY-FEEDS-R002: the host profile feed.

Conformance kit case host_profile_feed_ingests_observations: a
connector-facing feed delivers ``HostProfile{host_iri, cores,
memory_bytes, storage_bytes, capabilities}`` records into medulla
``SourceIngest`` as observation-class facts. Medulla owns the
``placement:HostProfile`` vocabulary; the SDK contract is the record
shape of the requirements row, run here through the ``mcp_tool``
source adapter against an in-process served connector (no network, no
production telemetry).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from scion_sdk.adapters.mcp_tool import McpToolSourceAdapter
from scion_sdk.manifest.live_contract import validate_live_tool_contract
from scion_sdk.manifest.presets import ToolPreset
from scion_sdk.ports.errors import MalformedSourceDataError
from scion_sdk.runner.syncing import SyncTarget, sync_stream
from scion_sdk.testing.results import assert_conformant
from scion_sdk.testing.sinks import InMemorySink, run_sink_suite

pytestmark = pytest.mark.spec("SCION-OBSERVABILITY-FEEDS-R002")

CONNECTOR = "observability-demo"
HOSTS_STREAM = "host-profiles"


def _tool_descriptor() -> dict[str, Any]:
    return {
        "name": "host_profiles",
        "inputSchema": {
            "type": "object",
            "properties": {"params_json": {"type": "string"}},
        },
        "outputSchema": {
            "type": "object",
            "required": ["status", "profiles", "cursor"],
            "properties": {
                "status": {"type": "string"},
                "profiles": {"type": "array"},
                "cursor": {"type": ["string", "null"]},
            },
        },
    }


class FakeHostSession:
    """A minimal ``McpSession`` serving canned host profile pages in sequence."""

    def __init__(
        self, *, descriptor: dict[str, Any], pages: Sequence[Mapping[str, Any]]
    ) -> None:
        self._descriptor = descriptor
        self._pages = list(pages)
        self.calls: list[dict[str, Any]] = []

    async def server_identity(self) -> Any:
        raise NotImplementedError

    async def list_tools(self) -> Sequence[Any]:
        return [self._descriptor]

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        assert name == self._descriptor["name"]
        self.calls.append(dict(arguments))
        assert self._pages, "fake session ran out of pages"
        return self._pages.pop(0)

    async def list_prompts(self) -> Sequence[Any]:
        raise NotImplementedError

    async def get_prompt(self, name: str, arguments: Mapping[str, str]) -> Any:
        raise NotImplementedError

    async def list_resources(self) -> Sequence[Any]:
        raise NotImplementedError

    async def read_resource(self, uri: str) -> str:
        raise NotImplementedError


def _preset() -> ToolPreset:
    return ToolPreset(
        name=HOSTS_STREAM,
        server="observability-mcp",
        tool="host_profiles",
        records_path="profiles",
        updated_field="updated_at",
        pagination="cursor",
        cursor_param="cursor",
        cursor_path="cursor",
    )


def _adapter(preset: ToolPreset, descriptor: dict[str, Any]) -> McpToolSourceAdapter:
    return McpToolSourceAdapter(
        preset,
        connector=CONNECTOR,
        mapping_reference=f"connector:{CONNECTOR}#mappings/HostProfile",
        tool_schema_sha256=validate_live_tool_contract(
            {"tools": [descriptor]}, tool_name=descriptor["name"]
        ).compatibility_sha256,
    )


def _host(
    host_id: str,
    *,
    cores: int = 8,
    memory_bytes: int = 32 * 2**30,
    storage_bytes: int = 2 * 2**40,
    capabilities: Sequence[str] = ("compute",),
    updated_at: str = "2026-11-24T08:00:00Z",
) -> dict[str, Any]:
    return {
        "id": host_id,
        "host_iri": f"placement:host-{host_id}",
        "cores": cores,
        "memory_bytes": memory_bytes,
        "storage_bytes": storage_bytes,
        "capabilities": list(capabilities),
        "updated_at": updated_at,
    }


def _page(*hosts: dict[str, Any], cursor: str | None = None) -> dict[str, Any]:
    return {"status": "ok", "profiles": list(hosts), "cursor": cursor}


async def test_host_profile_feed_ingests_observations() -> None:
    """The host profile feed delivers observation-class facts into
    ``SourceIngest``: one record per host, the five ``HostProfile`` fields
    intact, the ``HostProfile`` mapping reference named, the checkpoint
    accepted at the feed's end, and the sink conformance suite green."""
    descriptor = _tool_descriptor()
    preset = _preset()
    session = FakeHostSession(
        descriptor=descriptor,
        pages=[
            _page(_host("1"), _host("2", cores=16), cursor="page-2"),
            _page(_host("3", capabilities=("compute", "gpu")), cursor=None),
        ],
    )
    sink = InMemorySink()

    outcome = await sync_stream(
        session, _adapter(preset, descriptor), SyncTarget(CONNECTOR, sink, 3)
    )

    assert outcome.stream == HOSTS_STREAM
    assert outcome.records == 3
    assert outcome.accepted == 3
    assert outcome.exhausted is True
    assert len(session.calls) == 2

    batches = list(sink.batches.values())
    assert len(batches) == 2
    assert batches[0].expected_previous_checkpoint is None
    by_id: dict[str, Any] = {}
    for batch in batches:
        for record in batch.records:
            by_id[record.record_id] = record
    assert set(by_id) == {"1", "2", "3"}
    first = by_id["1"]
    assert first.stream == HOSTS_STREAM
    assert first.mapping_reference == f"connector:{CONNECTOR}#mappings/HostProfile"
    assert first.updated_at == "2026-11-24T08:00:00Z"
    assert first.provenance.connector == CONNECTOR
    # The observation-class fact: the five contract fields, intact.
    payload = first.payload
    assert payload["host_iri"] == "placement:host-1"
    assert payload["cores"] == 8
    assert payload["memory_bytes"] == 32 * 2**30
    assert payload["storage_bytes"] == 2 * 2**40
    assert payload["capabilities"] == ["compute"]
    assert by_id["3"].payload["capabilities"] == ["compute", "gpu"]
    # The SDK adds no fields of its own: the observation plus the two
    # pipeline identifiers are the whole payload.
    assert set(payload) == {
        "id",
        "host_iri",
        "cores",
        "memory_bytes",
        "storage_bytes",
        "capabilities",
        "updated_at",
    }

    status = await sink.source_status(CONNECTOR, HOSTS_STREAM)
    assert status.accepted_checkpoint is not None

    # The conformance kit run over the committed stream head: the receipt
    # binds the batch, a replay is idempotent, and a stale checkpoint is
    # refused.
    results = await run_sink_suite(lambda: InMemorySink(), batches[0])
    assert [result.check for result in results] == [
        "committed-receipt",
        "idempotent-submission",
        "checkpoint-compare-and-swap",
    ]
    assert_conformant(results)


async def test_host_profile_feed_refuses_malformed_records() -> None:
    """A record without a usable id refuses before any ingest, and the
    checkpoint does not advance."""
    descriptor = _tool_descriptor()
    session = FakeHostSession(
        descriptor=descriptor,
        pages=[_page({"host_iri": "placement:host-x", "cores": 4})],
    )
    sink = InMemorySink()

    with pytest.raises(MalformedSourceDataError, match="no usable id"):
        await sync_stream(
            session, _adapter(_preset(), descriptor), SyncTarget(CONNECTOR, sink, 1)
        )

    assert sink.batches == {}
    status = await sink.source_status(CONNECTOR, HOSTS_STREAM)
    assert status.accepted_checkpoint is None


async def test_host_profile_feed_refuses_a_non_list_page() -> None:
    """A page that does not match the declared records path refuses before
    any ingest."""
    descriptor = _tool_descriptor()
    session = FakeHostSession(
        descriptor=descriptor,
        pages=[{"status": "ok", "profiles": {}, "cursor": None}],
    )
    sink = InMemorySink()

    with pytest.raises(MalformedSourceDataError, match="records_path"):
        await sync_stream(
            session, _adapter(_preset(), descriptor), SyncTarget(CONNECTOR, sink, 1)
        )

    assert sink.batches == {}
    assert (
        await sink.source_status(CONNECTOR, HOSTS_STREAM)
    ).accepted_checkpoint is None
