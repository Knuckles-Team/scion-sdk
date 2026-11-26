"""The stream source adapter against a served broker connector (R005.41).

A real in-process MCP connector plays the recorded broker. The sink records the
order of engine commits and broker offset commits so the test can assert that
offsets are committed only after the engine accepts the page.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

import pytest
from fastmcp import Client, FastMCP
from medulla.generated.source_ingestion import (
    SourceIngestionReceipt,
    SourceIngestionRequest,
)

from scion_sdk.adapters.stream import (
    StreamRefusal,
    StreamRunOutcome,
    StreamSourceAdapter,
)
from scion_sdk.runner.syncing import SyncTarget
from scion_sdk.testing.sinks import InMemorySink

DIGEST = hashlib.sha256(b"consume_records").hexdigest()
LOG: list[str]


class _Broker:
    def __init__(self, count: int, *, fail_commit: bool = False) -> None:
        self.records = [
            {"topic": "orders", "partition": 0, "offset": i, "value": f"v{i}"}
            for i in range(count)
        ]
        self.committed: list[dict[str, Any]] = []
        self.fail_commit = fail_commit

    def server(self) -> FastMCP:
        mcp = FastMCP("kafka-fixture")

        @mcp.tool()
        def consume_records(
            max_records: int, offsets: dict[str, int]
        ) -> dict[str, Any]:
            """Poll records after the given next offsets."""
            start = offsets.get("orders/0", 0)
            return {"records": self.records[start : start + max_records]}

        @mcp.tool()
        def commit_offsets(offsets: list[dict[str, Any]]) -> dict[str, Any]:
            """Commit broker offsets."""
            LOG.append("broker_commit")
            if self.fail_commit:
                raise RuntimeError("broker down")
            self.committed.extend(offsets)
            return {"ok": True}

        return mcp


class _Session:
    def __init__(self, client: Client, *, down: bool = False) -> None:
        self._client = client
        self._down = down

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        if self._down:
            raise ConnectionError("unreachable")
        result = await self._client.call_tool(name, dict(arguments))
        return result.structured_content or json.loads(result.content[0].text)


class _OrderedSink(InMemorySink):
    def __init__(self, *, reject: bool = False) -> None:
        super().__init__()
        self.reject = reject

    async def submit(self, batch: SourceIngestionRequest) -> SourceIngestionReceipt:
        LOG.append("engine_commit")
        if self.reject:
            raise ValueError("engine refused the page")
        return await super().submit(batch)


def _adapter(page_messages: int = 2) -> StreamSourceAdapter:
    return StreamSourceAdapter(
        connector="kafka-mcp",
        stream="orders",
        server="kafka-fixture",
        tool_schema_sha256=DIGEST,
        mapping_reference="stream-records",
        page_messages=page_messages,
    )


@pytest.fixture(autouse=True)
def _log() -> None:
    global LOG
    LOG = []


def _target(sink: InMemorySink) -> SyncTarget:
    return SyncTarget("kafka-mcp", sink, max_pages=1)


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.41")
async def test_offset_commits_after_accepted_checkpoint() -> None:
    broker, sink = _Broker(3), _OrderedSink()
    async with Client(broker.server()) as client:
        session = _Session(client)
        first = await _adapter().run_once(session, _target(sink))
        assert LOG == ["engine_commit", "broker_commit"]
        assert isinstance(first, StreamRunOutcome)
        assert first.records == 2
        assert broker.committed == [{"topic": "orders", "partition": 0, "offset": 2}]
        second = await _adapter().run_once(session, _target(sink))
        assert second.records == 1
        assert broker.committed[-1]["offset"] == 3
        third = await _adapter().run_once(session, _target(sink))
        assert third.records == 0
        assert LOG.count("broker_commit") == 2


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.41")
async def test_rejected_page_commits_no_offsets_and_replays() -> None:
    broker = _Broker(2)
    async with Client(broker.server()) as client:
        session = _Session(client)
        with pytest.raises(StreamRefusal) as refused:
            await _adapter().run_once(session, _target(_OrderedSink(reject=True)))
        assert refused.value.code == "page_rejected"
        assert LOG == ["engine_commit"]
        assert broker.committed == []
        replay = await _adapter().run_once(session, _target(_OrderedSink()))
        assert replay.records == 2


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.41")
async def test_unreachable_broker_is_refused_and_page_size_is_bounded() -> None:
    broker = _Broker(1)
    async with Client(broker.server()) as client:
        with pytest.raises(StreamRefusal) as refused:
            await _adapter().run_once(
                _Session(client, down=True), _target(_OrderedSink())
            )
        assert refused.value.code == "broker_unreachable"
        assert LOG == []
    with pytest.raises(ValueError, match="batch bound"):
        _adapter(page_messages=1025)
    with pytest.raises(ValueError, match="unknown"):
        StreamRefusal("other")
