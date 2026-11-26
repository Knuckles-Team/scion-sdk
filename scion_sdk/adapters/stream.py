"""Stream source adapter: read broker records, submit pages, commit after accept.

SCION-SOURCE-INGEST-R005.41. A broker connector serves a ``consume`` tool and a
``commit offsets`` tool. One :meth:`StreamSourceAdapter.run_once` polls at most
``page_messages`` records, submits them to the engine as one ``records`` page
through the shared page-commit path, and only then calls the commit tool with
the offsets of the accepted checkpoint. A crash before the engine answers leaves
the broker offsets uncommitted, so the page replays and the engine de-duplicates
by cursor. The engine commit is the only write path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Final

from medulla.generated.source_ingestion import (
    SourceCheckpoint,
    SourceIngestionMode,
    SourceRecord,
    SourceRecordProvenance,
)

from scion_sdk.contracts import RecordPage
from scion_sdk.ports.errors import MalformedSourceDataError
from scion_sdk.ports.session import McpSession
from scion_sdk.runner.syncing import SyncTarget, commit_page
from scion_sdk.schema_contract import SchemaContract

__all__ = ["StreamRefusal", "StreamRunOutcome", "StreamSourceAdapter"]

#: The engine's batch bound for the ``records`` kind.
MAX_PAGE_MESSAGES: Final = 1024
_REFUSALS: Final = frozenset({"broker_unreachable", "page_rejected"})
_RECORD_CONTRACT: Final = SchemaContract.from_schema(
    {
        "type": "object",
        "required": ["topic", "partition", "offset"],
        "properties": {
            "topic": {"type": "string"},
            "partition": {"type": "integer"},
            "offset": {"type": "integer"},
        },
    },
    identifier_fields=("topic", "partition", "offset"),
)


class StreamRefusal(RuntimeError):
    """A stream page was refused; ``code`` is ``broker_unreachable`` or ``page_rejected``."""

    def __init__(self, code: str) -> None:
        if code not in _REFUSALS:
            raise ValueError("unknown stream refusal code")
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class StreamRunOutcome:
    """What one run committed: records accepted and offsets committed at the broker."""

    records: int
    accepted: int
    committed_offsets: Mapping[str, int]
    checkpoint: SourceCheckpoint | None


class StreamSourceAdapter:
    """Poll a broker through MCP tools and commit offsets after engine acceptance."""

    kind = "stream"

    def __init__(
        self,
        *,
        connector: str,
        stream: str,
        server: str,
        tool_schema_sha256: str,
        mapping_reference: str,
        page_messages: int,
        consume_tool: str = "consume_records",
        commit_tool: str = "commit_offsets",
    ) -> None:
        if not 1 <= page_messages <= MAX_PAGE_MESSAGES:
            raise ValueError("page_messages must be within the engine batch bound")
        if not (connector and stream and tool_schema_sha256 and mapping_reference):
            raise ValueError("stream adapter identity is incomplete")
        self._connector = connector
        self._stream = stream
        self._server = server
        self._pinned = tool_schema_sha256
        self._mapping_reference = mapping_reference
        self._page_messages = page_messages
        self._consume_tool = consume_tool
        self._commit_tool = commit_tool

    async def run_once(
        self, session: McpSession, target: SyncTarget
    ) -> StreamRunOutcome:
        """Poll one page, commit it to the engine, then commit broker offsets."""
        if target.schema_contract is None:
            target = replace(target, schema_contract=_RECORD_CONTRACT)
        status = await target.sink.source_status(target.connector, self._stream)
        previous = status.accepted_checkpoint
        raw = await self._poll(session, previous)
        if not raw:
            return StreamRunOutcome(0, 0, {}, previous)
        page = self._page(raw, previous)
        try:
            receipt = await commit_page(
                page, target, expected_previous_checkpoint=previous
            )
        except Exception:
            raise StreamRefusal("page_rejected") from None
        offsets = _page_offsets(raw, receipt.accepted_checkpoint)
        await self._commit_offsets(session, offsets)
        return StreamRunOutcome(
            len(raw), receipt.affected_count, offsets, receipt.accepted_checkpoint
        )

    async def _poll(
        self, session: McpSession, previous: SourceCheckpoint | None
    ) -> list[Mapping[str, Any]]:
        arguments = {
            "max_records": self._page_messages,
            "offsets": _checkpoint_offsets(previous),
        }
        try:
            result = await session.call_tool(self._consume_tool, arguments)
        except Exception:
            raise StreamRefusal("broker_unreachable") from None
        records = result.get("records") if isinstance(result, Mapping) else None
        if not isinstance(records, list) or len(records) > self._page_messages:
            raise MalformedSourceDataError("broker page is malformed or oversized")
        return [_checked(item) for item in records]

    async def _commit_offsets(
        self, session: McpSession, offsets: Mapping[str, int]
    ) -> None:
        entries = [
            {
                "topic": key.rpartition("/")[0],
                "partition": int(key.rpartition("/")[2]),
                "offset": value,
            }
            for key, value in sorted(offsets.items())
        ]
        try:
            await session.call_tool(self._commit_tool, {"offsets": entries})
        except Exception:
            raise StreamRefusal("broker_unreachable") from None

    def _page(
        self, raw: list[Mapping[str, Any]], previous: SourceCheckpoint | None
    ) -> RecordPage:
        merged = dict(_checkpoint_offsets(previous))
        merged.update(_page_offsets(raw, None))
        checkpoint = SourceCheckpoint(stream=self._stream, position={"offsets": merged})
        return RecordPage(
            records=tuple(self._record(item) for item in raw),
            mode=SourceIngestionMode.DELTA,
            strict_schema=True,
            checkpoint=checkpoint,
            exhausted=len(raw) < self._page_messages,
            schema_contract=_RECORD_CONTRACT,
        )

    def _record(self, item: Mapping[str, Any]) -> SourceRecord:
        topic, partition, offset = item["topic"], item["partition"], item["offset"]
        return SourceRecord(
            stream=self._stream,
            record_id=f"{topic}/{partition}/{offset}",
            mapping_reference=self._mapping_reference,
            payload=dict(item),
            provenance=SourceRecordProvenance(
                adapter_kind=self.kind,
                connector=self._connector,
                server=self._server,
                source_uri=f"stream://{topic}",
                tool=self._consume_tool,
                tool_schema_sha256=self._pinned,
            ),
        )


def _checked(item: object) -> Mapping[str, Any]:
    if (
        not isinstance(item, Mapping)
        or not isinstance(item.get("topic"), str)
        or not item["topic"]
        or "/" in item["topic"]
        or type(item.get("partition")) is not int
        or type(item.get("offset")) is not int
        or item["partition"] < 0
        or item["offset"] < 0
    ):
        raise MalformedSourceDataError("broker record lacks topic, partition or offset")
    return item


def _checkpoint_offsets(checkpoint: SourceCheckpoint | None) -> dict[str, int]:
    position = checkpoint.position if checkpoint is not None else None
    offsets = position.get("offsets") if isinstance(position, Mapping) else None
    return dict(offsets) if isinstance(offsets, Mapping) else {}


def _page_offsets(
    raw: list[Mapping[str, Any]], accepted: SourceCheckpoint | None
) -> dict[str, int]:
    """The next offset per topic partition: the page's, bounded by the accepted one."""
    next_offsets: dict[str, int] = {}
    for item in raw:
        key = f"{item['topic']}/{item['partition']}"
        next_offsets[key] = max(next_offsets.get(key, 0), item["offset"] + 1)
    if accepted is None:
        return next_offsets
    accepted_offsets = _checkpoint_offsets(accepted)
    return {k: v for k, v in next_offsets.items() if accepted_offsets.get(k) == v}
